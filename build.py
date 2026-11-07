"""Pull the Electoral Commission results feed and rebuild election.html.

Usage:  python3 build.py            (fetches https://media.election.net.nz/xml/)
        python3 build.py --local DIR (uses XML files already saved in DIR)

The feed sends no CORS headers, so the page can't fetch it in the browser.
This script embeds a snapshot of the data into template.html instead.
"""
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://media.election.net.nz/xml/"
HERE = Path(__file__).resolve().parent
MAORI = {"Hauraki-Waikato", "Ikaroa-Rāwhiti", "Tāmaki Makaurau", "Te Tai Hauāuru",
         "Te Tai Tokerau", "Te Tai Tonga", "Waiariki"}


def loader():
    if "--local" in sys.argv:
        d = Path(sys.argv[sys.argv.index("--local") + 1])
        return lambda name: ET.parse(d / Path(name).name).getroot()

    def fetch(name):
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "election-viz-build"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return ET.fromstring(r.read())
    return fetch


def fetch_text(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "election-viz-build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def voting_places(master, n_electorates):
    """Which voting places have reported. The feed adds eNN/votingplaces/NN-XXYYY.xml as each
    place reports a result for electorate NN. File contents never change, so each one is
    fetched once and remembered in vpcache.json."""
    import re
    cache_path = HERE / "vpcache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    def listing(e):
        # Apache index rows: <a href="01-01005.xml">…</a></td><td align="right">2026-11-07 22:51  </td>
        try:
            html = fetch_text(f"e{e:02d}/votingplaces/")
        except Exception:
            return []
        rows = re.findall(r'href="(\d\d-\d+\.xml)".*?<td align="right">\s*([\d-]+ [\d:]+)', html)
        return [(f"e{e:02d}/votingplaces/{f}", m) for f, m in rows]

    with ThreadPoolExecutor(12) as ex:
        files = [f for fs in ex.map(listing, range(1, n_electorates + 1)) for f in fs]

    def read(item):
        path, mtime = item
        try:
            x = ET.fromstring(fetch_text(path))
            return path, {"vp": x.get("vp_id"), "e": int(x.get("e_no") or 0),
                          "t": x.findtext("time_received") or "",
                          "n": num(x.find("statistics"), "total_issued_ballot_papers"), "m": mtime,
                          "pv": {p.get("p_no"): num(p, "votes") for p in x.find("partyvotes") or []},
                          "cv": {c.get("c_no"): num(c, "votes") for c in x.find("candidatevotes") or []}}
        except Exception:
            return path, None

    # Files are rewritten when a place's result is updated, so re-read any whose modified time changed.
    stale = [(f, m) for f, m in files if cache.get(f, {}).get("m") != m or "pv" not in cache.get(f, {})]
    if stale:
        with ThreadPoolExecutor(16) as ex:
            for path, rec in ex.map(read, stale):
                if rec:
                    cache[path] = rec
        cache_path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")

    got = {}
    for rec in cache.values():
        g = got.setdefault(rec["vp"], {"t": rec["t"], "n": 0, "k": 0, "r": set()})
        g["n"] += rec["n"]
        g["k"] += 1
        g["r"].add(rec["e"])
        g["t"] = max(g["t"], rec["t"])  # most recent result received from this place
    out = []
    for v in master:
        vid = v.get("vp_id")
        lat, lon = v.findtext("vp_lat"), v.findtext("vp_lon")
        g = got.get(vid)
        out.append({
            "id": vid, "e": int(v.get("vp_e_no") or 0), "a": v.findtext("vp_address") or "",
            "lat": round(float(lat), 5) if lat else None, "lon": round(float(lon), 5) if lon else None,
            "t": g["t"] if g else None,   # first result received
            "n": g["n"] if g else 0,      # ballot papers issued, all electorates
            "k": g["k"] if g else 0,      # electorate results received from this place
            "r": sorted(g["r"]) if g else [],  # electorates it has reported results for
        })
    return out, cache


def fit_transfers(cache, electorates):
    """How each party's voters split their candidate vote, per electorate, estimated from voting-place results
    by iterative proportional fitting: find shares T[party][candidate] such that, at every place, party votes
    x shares reproduce that place's candidate votes. A "none" column absorbs party voters with no valid
    candidate vote. Parties under 1.5% of the electorate's party vote are grouped as "other"."""
    import hashlib
    import numpy as np
    sig = hashlib.md5(json.dumps(["v3-noboot"] + sorted((k, v.get("m")) for k, v in cache.items())).encode()).hexdigest()
    path = HERE / "transfers.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("sig") == sig:
            return saved["transfer"]
    except (OSError, ValueError):
        pass
    BOOT = 0  # bootstrap refits for uncertainty ranges; switched off (the page no longer shows them)

    def fit(P, C):
        T = np.tile(C.sum(0) / C.sum(), (P.shape[1], 1))
        for _ in range(200):
            f = P[:, :, None] * T[None, :, :]
            for _ in range(4):
                f *= np.divide(C, f.sum(1), out=np.zeros_like(C), where=f.sum(1) > 0)[:, None, :]
                f *= np.divide(P, f.sum(2), out=np.zeros_like(P), where=f.sum(2) > 0)[:, :, None]
            num_ = f.sum(0)
            T_new = np.divide(num_, num_.sum(1, keepdims=True), out=T.copy(), where=num_.sum(1, keepdims=True) > 0)
            done = np.abs(T_new - T).max() < 1e-5
            T = T_new
            if done:
                break
        return T

    by_e = {}
    for rec in cache.values():
        if rec.get("pv") is not None:
            by_e.setdefault(rec["e"], []).append(rec)
    result = {}
    for e in electorates:
        recs = by_e.get(e["id"], [])
        if len(recs) < 5:
            continue
        tot = {}
        for r in recs:
            for pid, v in r["pv"].items():
                tot[pid] = tot.get(pid, 0) + v
        allv = sum(tot.values()) or 1
        parties = [pid for pid, v in sorted(tot.items(), key=lambda kv: -kv[1]) if v / allv >= 0.015]
        cands = [c["id"] for c in e["candidates"] if c.get("id")]
        if not parties or not cands:
            continue
        P = np.array([[r["pv"].get(pid, 0) for pid in parties] + [sum(v for pid, v in r["pv"].items() if pid not in parties)] for r in recs], float)
        C = np.array([[r["cv"].get(cid, 0) for cid in cands] for r in recs], float)
        none = np.maximum(P.sum(1) - C.sum(1), 0)
        C = np.column_stack([C, none])
        # make each place's margins agree before fitting
        scale = np.divide(P.sum(1), C.sum(1), out=np.ones(len(recs)), where=C.sum(1) > 0)
        C = C * scale[:, None]
        T = fit(P, C)
        # Bootstrap: refit on resampled voting places to show how firm the estimate is
        rng = np.random.default_rng(int(e["id"]))
        boot = []
        for _ in range(BOOT):
            idx = rng.integers(0, len(recs), len(recs))
            Tb = fit(P[idx], C[idx])
            boot.append([int(round(float(x) * 1000)) for x in Tb.ravel()])
        result[str(e["id"])] = {"parties": parties + ["other"], "cands": cands + ["none"],
                                "T": [[round(float(x), 4) for x in row] for row in T],
                                "places": len(recs),
                                "boot": boot}  # BOOT refits, each the T matrix flattened row-major, in thousandths
    path.write_text(json.dumps({"sig": sig, "transfer": result}, separators=(",", ":")), encoding="utf-8")
    return result


def coastline():
    """NZ land outline from Natural Earth 1:50m (world-atlas). Fetched once, saved as nz.json."""
    path = HERE / "nz.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "election-viz-build"}), timeout=60) as r:
        topo = json.loads(r.read())
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        pts = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a if not pts else a[1:])
        return pts

    nz = next(g for g in topo["objects"]["countries"]["geometries"] if str(g.get("id")) == "554")
    polys = nz["arcs"] if nz["type"] == "MultiPolygon" else [nz["arcs"]]
    rings = []
    for poly in polys:
        outer = ring(poly[0])
        lons = [p[0] % 360 for p in outer]
        lats = [p[1] for p in outer]
        if not (165 <= min(lons) and max(lons) <= 185 and -48.5 <= min(lats) and max(lats) <= -33):
            continue  # drop the distant outlying islands
        rings.append([[round(p[0] % 360, 3), round(p[1], 3)] for p in outer])
    path.write_text(json.dumps(rings, separators=(",", ":")), encoding="utf-8")
    return rings


def num(el, tag, cast=int, default=0):
    t = el.findtext(tag)
    try:
        return cast(t)
    except (TypeError, ValueError):
        return default


def main():
    get = loader()
    election = get("election.xml")
    parties_x = get("parties.xml")
    cands_x = get("candidates.xml")
    elects_x = get("electorates.xml")
    stats_x = get("statistics.xml")

    parties = {}
    for p in parties_x:
        parties[p.get("p_no")] = {
            "id": p.get("p_no"),
            "abbrev": p.findtext("abbrev") or "",
            "short": p.findtext("short_name") or "",
            "name": p.findtext("party_name") or "",
            "registered": p.findtext("registered") == "yes",
            "votes": 0, "pct": 0.0, "list": 0, "elect": 0, "seats": 0,
        }
    for p in election.find("partystatus"):
        d = parties.setdefault(p.get("p_no"), {"id": p.get("p_no"), "abbrev": p.get("p_no"),
                                                "short": "", "name": "", "registered": True})
        d.update(votes=num(p, "votes"), pct=num(p, "percent_votes", float),
                 list=num(p, "party_seats"), elect=num(p, "candidate_seats"),
                 seats=num(p, "total_seats"))

    cands = {}
    for c in cands_x:
        cands[c.get("c_no")] = {
            "name": c.findtext("candidate_name") or "",
            "party": c.findtext("party") or "",
            "list": num(c, "list_no"),
        }

    names = [(e.get("e_no"), e.findtext("electorate_name")) for e in elects_x]

    def load_e(item):
        eno, name = item
        x = get(f"e{int(eno):02d}/e{int(eno):02d}.xml")
        s = x.find("statistics")
        cv = []
        for c in x.find("candidatevotes") or []:
            info = cands.get(c.get("c_no"), {"name": "Unknown", "party": "", "list": 0})
            cv.append({"id": c.get("c_no"), "name": info["name"], "party": info["party"], "list": info["list"],
                       "votes": num(c, "votes")})
        cv.sort(key=lambda c: -c["votes"])
        pv = {p.get("p_no"): num(p, "votes") for p in x.find("partyvotes") or []}
        return {
            "id": int(eno), "name": name, "maori": name in MAORI,
            "final": x.get("final") == "true", "updated": x.get("updated"),
            "places": num(s, "total_voting_places"),
            "counted": num(s, "total_voting_places_counted"),
            "pctCounted": num(s, "percent_voting_places_counted", float),
            "cast": num(s, "total_votes_cast"),
            "pctCast": num(s, "percent_votes_cast", float),
            "partyInformal": num(s, "total_party_informals"),
            "candInformal": num(s, "total_candidate_informals"),
            "candidates": cv, "partyVotes": pv,
        }

    with ThreadPoolExecutor(12) as ex:
        electorates = list(ex.map(load_e, names))

    st = election.find("statistics")
    data = {
        "updated": election.get("updated"),
        "stats": {
            "pctPlaces": num(st, "percent_voting_places_counted", float),
            "placesCounted": num(st, "total_voting_places_counted"),
            "votesCast": num(st, "total_votes_cast"),
            "pctVotes": num(st, "percent_votes_cast", float),
            "electoratesFinal": num(st, "total_electorates_final"),
            "specials": num(st, "total_special_votes"),
            "electors": num(stats_x, "total_electors"),
            "candidates": num(stats_x, "total_candidates"),
        },
        "parties": list(parties.values()),
        "electorates": electorates,
    }
    if "--local" not in sys.argv:
        data["places"], vpc = voting_places(get("votingplaces.xml"), len(names))
        try:
            data["transfer"] = fit_transfers(vpc, electorates)
        except Exception as exc:
            print("Transfer fit failed:", exc, flush=True)
        # 2023 party vote at the same voting place (matched on Electoral Commission voting place ID)
        try:
            r23 = json.loads((HERE / "results2023.json").read_text(encoding="utf-8"))
            data["r23"] = {"cols": r23["cols"], "national": r23["national"]}
            for pl in data["places"]:
                v = r23["places"].get(pl["id"])
                if v:
                    pl["v23"] = v
        except (OSError, ValueError, KeyError):
            pass
        # 2023 ordinary and special party votes by electorate, for the specials simulator
        try:
            data["sp23"] = json.loads((HERE / "specials2023.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        try:
            data["coast"] = coastline()
        except Exception as exc:
            print("Coastline unavailable:", exc, flush=True)
            data["coast"] = []

    # Trend history: the feed only gives the current count, so keep our own log of each new snapshot.
    hist_path = HERE / "history.json"
    try:
        history = json.loads(hist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    if not history or history[-1]["t"] != data["updated"]:
        total = sum(p.get("votes", 0) for p in data["parties"]) or 1
        history.append({
            "t": data["updated"],
            "c": data["stats"]["pctPlaces"],
            # computed from raw votes: the feed's 2dp percentages add rounding noise to small moves
            "p": {p["id"]: round(100 * p["votes"] / total, 4) for p in data["parties"] if p.get("votes")},
        })
        history.sort(key=lambda h: h["t"])
        hist_path.write_text(json.dumps(history, separators=(",", ":")), encoding="utf-8")
    data["history"] = history

    # The page no longer has the map or trend lines: embed only what the specials model needs.
    data.pop("coast", None)
    data.pop("history", None)
    data["places"] = [{k: pl[k] for k in ("id", "e", "t", "n", "v23") if k in pl} for pl in data.get("places", [])]

    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (HERE / "data.json").write_text(raw, encoding="utf-8")
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    out = tpl.replace("/*__DATA__*/null", raw.replace("</", "<\\/"))
    (HERE / "election.html").write_text(out, encoding="utf-8")
    if "--static" in sys.argv:
        # Self-contained page served by GitHub Pages
        pub = HERE
        (pub / "index.html").write_text(out.replace("/*__STATIC__*/false", "true"), encoding="utf-8")
        print(f"Wrote {pub / 'index.html'} ({(pub / 'index.html').stat().st_size // 1024} KB)", flush=True)
    seats = sum(p["seats"] for p in data["parties"])
    print(f"{time.strftime('%H:%M:%S')} built election.html + data.json: feed updated {data['updated']}, "
          f"{data['stats']['pctPlaces']}% counted, {seats} seats, {len(electorates)} electorates", flush=True)


def serve(port=8765, max_age=30):
    """Serve this folder at http://localhost:<port>/election.html.
    Any request for the page or data.json re-pulls the feed if our copy is older than max_age seconds."""
    import functools, http.server, threading, time

    lock = threading.Lock()
    last = [0.0]

    def refresh():
        with lock:
            if time.time() - last[0] < max_age:
                return
            try:
                main()
            except Exception as exc:  # keep serving the last good data
                print("Refresh failed:", exc, flush=True)
            last[0] = time.time()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/election.html", "/data.json"):
                refresh()
            if path == "/":
                self.path = "/election.html"
            super().do_GET()

        # One-off import of 2023 results by voting place, posted from the browser
        # (the 2023 archive blocks scripted downloads). Only accepts JSON on this one path.
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "https://archive.electionresults.govt.nz")
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_POST(self):
            targets = {"/import-2023": ("results2023.json", "places"),
                       "/import-specials2023": ("specials2023.json", "electorates")}
            if self.path not in targets:
                self.send_error(404)
                return
            fname, required = targets[self.path]
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 5_000_000))
            try:
                parsed = json.loads(body)
                assert isinstance(parsed, dict) and required in parsed
            except Exception:
                self.send_error(400)
                return
            (HERE / fname).write_text(json.dumps(parsed, separators=(",", ":")), encoding="utf-8")
            last[0] = 0  # rebuild on next request
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "https://archive.electionresults.govt.nz")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            self.wfile.write(b"saved")

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    def background():  # keep the trend history continuous even with no page open
        while True:
            refresh()
            time.sleep(60)

    threading.Thread(target=background, daemon=True).start()
    handler = functools.partial(Handler, directory=str(HERE))
    print(f"Serving http://localhost:{port}/election.html (fresh feed on every check)", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else main()
