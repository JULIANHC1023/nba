# NBA 每日情報產生器 (Julian)  —  python3 gen.py --out report.html [--notes notes.json] [--ledger bets.csv] [--json out.json]
# 資料：ESPN 公開 API（比分、賽程、box score、傷兵、新聞、DraftKings 獨贏盤）。
import json, math, sys, argparse, datetime as dt, html, csv, urllib.request, concurrent.futures as cf
import numpy as np

TW = dt.timezone(dt.timedelta(hours=8))
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ZH = {"ATL":"老鷹","BOS":"塞爾提克","BKN":"籃網","CHA":"黃蜂","CHI":"公牛","CLE":"騎士","DAL":"獨行俠","DEN":"金塊","DET":"活塞","GS":"勇士",
      "HOU":"火箭","IND":"溜馬","LAC":"快艇","LAL":"湖人","MEM":"灰熊","MIA":"熱火","MIL":"公鹿","MIN":"灰狼","NO":"鵜鶘","NY":"尼克",
      "OKC":"雷霆","ORL":"魔術","PHI":"76人","PHX":"太陽","POR":"拓荒者","SAC":"國王","SA":"馬刺","TOR":"暴龍","UTAH":"爵士","WSH":"巫師"}
HCA_PTS = 2.0      # 主場優勢（分）
B2B_PTS = 1.8      # 背靠背第二場扣分
SIGMA = 12.5       # 比分差標準差
MKT_W = 0.30       # 模型對市場的拉力（最多 ±3pp）

def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "nba-brief/1.0"}), timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1: raise

def phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def esc(s): return html.escape(str(s), quote=True)
def dec(am):
    if am in (None, "", "OFF", "EVEN"): return 2.0 if am == "EVEN" else None
    am = float(str(am).replace("+", ""))
    return 1 + (am / 100 if am > 0 else 100 / -am)

def ml_of(c):
    o = (c.get("odds") or [{}])[0]; ml = o.get("moneyline") or {}
    def side(s):
        for k in ("close", "open"):
            try: return ml[s][k]["odds"]
            except Exception: pass
    return side("away"), side("home"), o.get("details", ""), o.get("overUnder")

# ---------- 1. 抓資料 ----------
def fetch_all(today_tw):
    start = dt.date(today_tw.year if today_tw.month >= 10 else today_tw.year - 1, 10, 1)
    end = today_tw + dt.timedelta(days=4)
    days = []; d = start
    while d <= end: days.append(d); d += dt.timedelta(days=1)
    with cf.ThreadPoolExecutor(16) as ex:
        sbs = list(ex.map(lambda d: get(f"{API}/scoreboard?dates={d:%Y%m%d}"), days))
    events = {}
    for sb in sbs:
        for e in sb.get("events", []):
            if e.get("season", {}).get("type") in (2, 3, 5) or e.get("season", {}).get("type") is None:
                events[e["id"]] = e
    done = [e for e in events.values() if e["competitions"][0]["status"]["type"]["completed"] and e["season"]["type"] == 2]
    with cf.ThreadPoolExecutor(16) as ex:
        sums = dict(zip([e["id"] for e in done], ex.map(lambda e: get(f"{API}/summary?event={e['id']}"), done)))
    inj = get(f"{API}/injuries"); news = get(f"{API}/news?limit=40")
    return events, done, sums, inj, news

# ---------- 2. 高階數據 ----------
def sv(stats, name):
    for s in stats:
        if s["name"] == name: return s["displayValue"]

def parse_game(e, s):
    c = e["competitions"][0]; per = c["status"].get("period", 4)
    tm = {}
    for x in c["competitors"]:
        tm[x["team"]["abbreviation"]] = {"pts": int(x["score"]), "ha": x["homeAway"]}
    for t in s["boxscore"]["teams"]:
        st = t["statistics"]; ab = t["team"]["abbreviation"]; T = tm[ab]
        T["fgm"], T["fga"] = map(int, sv(st, "fieldGoalsMade-fieldGoalsAttempted").split("-"))
        T["tpm"], T["tpa"] = map(int, sv(st, "threePointFieldGoalsMade-threePointFieldGoalsAttempted").split("-"))
        T["ftm"], T["fta"] = map(int, sv(st, "freeThrowsMade-freeThrowsAttempted").split("-"))
        T["orb"] = int(sv(st, "offensiveRebounds")); T["drb"] = int(sv(st, "defensiveRebounds"))
        T["tov"] = int(sv(st, "totalTurnovers") or sv(st, "turnovers"))
    a, b = list(tm)
    def poss(T, O): return T["fga"] + T["tov"] + 0.44 * T["fta"] - 1.07 * (T["orb"] / max(1, T["orb"] + O["drb"])) * (T["fga"] - T["fgm"])
    p = (poss(tm[a], tm[b]) + poss(tm[b], tm[a])) / 2
    home = [k for k in tm if tm[k]["ha"] == "home"][0]; away = [k for k in tm if k != home][0]
    players = []
    for side in s["boxscore"].get("players", []):
        ab = side["team"]["abbreviation"]; blk = side["statistics"][0]; L = blk["labels"]
        for a_ in blk["athletes"]:
            st = a_.get("stats") or []
            if not st or st[0] in ("", "0", None): continue
            r = dict(zip(L, st))
            try:
                fgm, fga = map(int, r["FG"].split("-")); ftm, fta = map(int, r["FT"].split("-"))
                v = dict(team=ab, name=a_["athlete"]["displayName"], id=a_["athlete"]["id"], min=float(r["MIN"]), pts=int(r["PTS"]),
                         reb=int(r["REB"]), ast=int(r["AST"]), stl=int(r["STL"]), blk=int(r["BLK"]), tov=int(r["TO"]), oreb=int(r["OREB"]),
                         dreb=int(r["DREB"]), pf=int(r["PF"]), fgm=fgm, fga=fga, ftm=ftm, fta=fta, tpm=int(r["3PT"].split("-")[0]))
            except Exception:
                continue
            v["gmsc"] = v["pts"] + 0.4*fgm - 0.7*fga - 0.4*(fta-ftm) + 0.7*v["oreb"] + 0.3*v["dreb"] + v["stl"] + 0.7*v["ast"] + 0.7*v["blk"] - 0.4*v["pf"] - v["tov"]
            players.append(v)
    date = dt.datetime.fromisoformat(c["date"].replace("Z", "+00:00"))
    return dict(id=e["id"], date=date, home=home, away=away, H=tm[home], A=tm[away], poss=p, mins=48 + 5*max(0, per-4), ot=max(0, per-4), players=players)

def ridge(games, teams, lam=4.0):
    idx = {t: i for i, t in enumerate(teams)}; n = len(teams)
    X = np.zeros((len(games), n + 1)); y = np.zeros(len(games))
    for k, g in enumerate(games):
        X[k, idx[g["home"]]] = 1; X[k, idx[g["away"]]] = -1; X[k, n] = 1
        y[k] = 100 * (g["H"]["pts"] - g["A"]["pts"]) / g["poss"]
    L = np.eye(n + 1) * lam; L[n, n] = 0
    beta = np.linalg.solve(X.T @ X + L, X.T @ y)
    return {t: float(beta[idx[t]]) for t in teams}, float(beta[n])

def team_table(games):
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    A = {t: dict(log=[], k={}) for t in teams}
    keys = ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "tov")
    for g in games:
        for me, op, home in (("H", "A", True), ("A", "H", False)):
            t = g["home"] if home else g["away"]; X, Y = g[me], g[op]
            A[t]["log"].append(dict(date=g["date"], opp=g["away"] if home else g["home"], home=home, pf=X["pts"], pa=Y["pts"], poss=g["poss"], mins=g["mins"]))
            for k in keys:
                A[t]["k"][k] = A[t]["k"].get(k, 0) + X[k]; A[t]["k"]["o_" + k] = A[t]["k"].get("o_" + k, 0) + Y[k]
    adj, hca = ridge(games, teams)
    R = {}
    for t in teams:
        lg = A[t]["log"]; K = A[t]["k"]; P = sum(x["poss"] for x in lg); M = sum(x["mins"] for x in lg)
        w = sum(x["pf"] > x["pa"] for x in lg)
        l5 = lg[-5:]; l5net = 100 * (sum(x["pf"] - x["pa"] for x in l5)) / sum(x["poss"] for x in l5)
        st = 0; last = lg[-1]["pf"] > lg[-1]["pa"]
        for x in reversed(lg):
            if (x["pf"] > x["pa"]) == last: st += 1
            else: break
        hw = sum(1 for x in lg if x["home"] and x["pf"] > x["pa"]); hl = sum(1 for x in lg if x["home"]) - hw
        aw = sum(1 for x in lg if not x["home"] and x["pf"] > x["pa"]); al = sum(1 for x in lg if not x["home"]) - aw
        R[t] = dict(g=len(lg), w=w, l=len(lg) - w, ortg=100*K["pts"]/P, drtg=100*K["o_pts"]/P, pace=48*P/M,
                    efg=(K["fgm"]+0.5*K["tpm"])/K["fga"], tovp=K["tov"]/P, orbp=K["orb"]/(K["orb"]+K["o_drb"]), ftr=K["ftm"]/K["fga"],
                    tpar=K["tpa"]/K["fga"], tpp=K["tpm"]/max(1, K["tpa"]),
                    oefg=(K["o_fgm"]+0.5*K["o_tpm"])/K["o_fga"], otovp=K["o_tov"]/P, drbp=K["drb"]/(K["drb"]+K["o_orb"]), oftr=K["o_ftm"]/K["o_fga"],
                    l5net=l5net, l5w=sum(x["pf"] > x["pa"] for x in l5), streak=("連勝 " if last else "連敗 ") + str(st),
                    home=f"{hw}-{hl}", away=f"{aw}-{al}", adj=adj[t], dates=[x["date"] for x in lg])
        R[t]["net"] = R[t]["ortg"] - R[t]["drtg"]
    for t in teams:
        R[t]["sos"] = float(np.mean([adj[x["opp"]] for x in A[t]["log"]]))
        R[t]["power"] = R[t]["adj"] + 0.15 * (R[t]["l5net"] - R[t]["net"])
    for k, rev in (("net", True), ("ortg", True), ("drtg", False), ("adj", True), ("pace", True), ("efg", True), ("oefg", False), ("tovp", False), ("orbp", True), ("ftr", True)):
        for i, t in enumerate(sorted(teams, key=lambda t: R[t][k], reverse=rev)): R[t][k + "_rk"] = i + 1
    lg_ortg = float(np.mean([R[t]["ortg"] for t in teams]))
    return R, lg_ortg, hca

def player_table(games):
    P = {}; team_games = {}
    for g in games:
        for t in (g["home"], g["away"]): team_games[t] = team_games.get(t, 0) + 1
        for v in g["players"]:
            q = P.setdefault(v["name"], dict(team=v["team"], gp=0, min=0, pts=0, reb=0, ast=0, gmsc=0, fga=0, fta=0, last=g["date"]))
            q["team"] = v["team"]; q["gp"] += 1; q["last"] = g["date"]
            for k in ("min", "pts", "reb", "ast", "gmsc", "fga", "fta"): q[k] += v[k]
    for n, q in P.items():
        gp = q["gp"]; q.update(mpg=q["min"]/gp, ppg=q["pts"]/gp, rpg=q["reb"]/gp, apg=q["ast"]/gp, gs=q["gmsc"]/gp,
                               ts=q["pts"]/max(1, 2*(q["fga"]+0.44*q["fta"])), share=gp/max(1, team_games.get(q["team"], 1)))
        # 缺陣影響（分）：GameScore 高於替補水準的部分 × 0.35，乘上出賽比例（長期缺陣已反映在球隊數據）
        q["impact"] = max(0.0, (q["gs"] - 7.0) * 0.35) if q["mpg"] >= 18 else 0.0
    return P

# ---------- 3. 走勢回測（每場只用賽前資料） ----------
def backtest(games, min_games=60):
    hit = n = 0; brier = 0.0; ll = 0.0; base_b = 0.0
    for k in range(min_games, len(games)):
        prev = games[:k]; g = games[k]
        teams = sorted({x["home"] for x in prev} | {x["away"] for x in prev})
        if g["home"] not in teams or g["away"] not in teams: continue
        adj, _ = ridge(prev, teams)
        m = (adj[g["home"]] - adj[g["away"]]) + HCA_PTS
        p = 0.5 + 0.85 * (phi(m / SIGMA) - 0.5)
        y = 1 if g["H"]["pts"] > g["A"]["pts"] else 0
        hit += (p > 0.5) == (y == 1); n += 1; brier += (p - y) ** 2; base_b += (0.56 - y) ** 2
        ll += -math.log(p if y else 1 - p)
    return dict(n=n, acc=hit / max(1, n), brier=brier / max(1, n), base_brier=base_b / max(1, n), logloss=ll / max(1, n))

# ---------- 4. 單場預測 ----------
def rest_info(team, tip, R, sched):
    played = [d for d in R[team]["dates"] if d < tip] + [d for d in sched.get(team, []) if d < tip]
    if not played: return 3, False, False
    last = max(played); gap = (tip.astimezone(dt.timezone(dt.timedelta(hours=-5))).date() - last.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()).days
    four = [d for d in played if (tip - d).days < 4]
    return gap, gap == 1, len(four) >= 2

WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LONG = ("week", "month", "season", "surgery", "re-evaluated", "reevaluated", "timetable", "indefinitely", "torn", "fracture")
def injury_list(team, inj, P, tip=None):
    out = []
    for t in inj.get("injuries", []):
        for i in t["injuries"]:
            a = i["athlete"]; nm = a["displayName"]
            ab = (a.get("team") or {}).get("abbreviation") or t.get("abbreviation")
            q = P.get(nm)
            if q is None or q["team"] != team: continue
            st = i["status"]; cm = (i.get("shortComment") or "")
            w = 1.0 if st == "Out" else 0.5
            if "probable" in cm.lower(): w = 0.15
            if "questionable" in cm.lower() and st == "Out": w = 0.5
            if tip is not None and w == 1.0 and not any(k in cm.lower() for k in LONG):
                gday = WD[tip.astimezone(dt.timezone(dt.timedelta(hours=-5))).weekday()]
                said = [d for d in WD if d in cm]
                if said and gday not in said: w = 0.5   # 說明是針對別天的比賽：當作出賽成疑
            eff = q["impact"] * w * min(1.0, q["share"])
            out.append(dict(name=nm, status=("缺陣" if w == 1 else "成疑" if w == 0.5 else "可能出賽"), w=w, ppg=q["ppg"], impact=eff,
                            star=q["impact"] >= 2.5, comment=cm, date=i.get("date", "")[:10]))
    return sorted(out, key=lambda x: -x["impact"])

def predict(e, R, P, inj, lg_ortg, sched):
    c = e["competitions"][0]; tip = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
    h = [x for x in c["competitors"] if x["homeAway"] == "home"][0]["team"]["abbreviation"]
    a = [x for x in c["competitors"] if x["homeAway"] == "away"][0]["team"]["abbreviation"]
    if h not in R or a not in R: return None
    gh, b2b_h, f_h = rest_info(h, tip, R, sched); ga, b2b_a, f_a = rest_info(a, tip, R, sched)
    ih = injury_list(h, inj, P, tip); ia = injury_list(a, inj, P, tip)
    pace = (R[h]["pace"] + R[a]["pace"]) / 2
    base = (R[h]["power"] - R[a]["power"]) * pace / 100
    rest = (-B2B_PTS if b2b_h else 0) + (B2B_PTS if b2b_a else 0) + (-0.7 if f_h else 0) + (0.7 if f_a else 0)
    injadj = -sum(x["impact"] for x in ih) + sum(x["impact"] for x in ia)
    margin = base + HCA_PTS + rest + injadj
    p_model = 0.5 + 0.85 * (phi(margin / SIGMA) - 0.5)
    ma, mh, spread, ou = ml_of(c)
    da, dh = dec(ma), dec(mh)
    p_mkt = None
    if da and dh:
        p_mkt = (1/dh) / (1/dh + 1/da)
    if p_mkt is not None:
        p_final = p_mkt + max(-0.03, min(0.03, MKT_W * (p_model - p_mkt))); basis = "市場"
    else:
        p_final = p_model; basis = "模型"
    exp_h = pace * (R[h]["ortg"] + R[a]["drtg"] - lg_ortg) / 100 + HCA_PTS / 2 + (rest + injadj) / 2
    exp_a = pace * (R[a]["ortg"] + R[h]["drtg"] - lg_ortg) / 100 - HCA_PTS / 2 - (rest + injadj) / 2
    fav = h if p_final >= 0.5 else a; pf = max(p_final, 1 - p_final)
    return dict(id=e["id"], tip=tip, home=h, away=a, ml_a=ma, ml_h=mh, spread=spread, ou=ou, p_mkt=p_mkt, p_model=p_model, p_home=p_final,
                basis=basis, fav=fav, pf=pf, thr=1.05 / pf, margin=margin, base=base, rest=rest, injadj=injadj, pace=pace,
                exp_h=exp_h, exp_a=exp_a, b2b_h=b2b_h, b2b_a=b2b_a, gap_h=gh, gap_a=ga, inj_h=ih, inj_a=ia)

# ---------- 5. 下注規則 ----------
def ceil10(x): return int(math.ceil(x / 10.0) * 10)
def risky(g, team):
    b2b = g["b2b_h"] if team == g["home"] else g["b2b_a"]
    inj = g["inj_h"] if team == g["home"] else g["inj_a"]
    return b2b or any(x["star"] and x["w"] < 1 for x in inj)

def bet_plan(D1, tw_odds):
    recs = []
    for g in D1:
        o = tw_odds.get(g["fav"])
        if not o: continue
        ev = g["pf"] * o - 1
        if ev >= 0.05:
            sure = ev >= 0.10 and g["pf"] >= 0.65 and not risky(g, g["fav"])
            target = 800 if sure else 500
            stake = ceil10(target / (o - 1)); net = target
            if stake > 500: stake = 500; net = round(500 * (o - 1))
            recs.append(dict(team=g["fav"], g=g, odds=o, ev=ev, tier="有把握" if sure else "一般", stake=stake, net=net))
    return sorted(recs, key=lambda r: -r["g"]["pf"])

# ---------- 6. 記帳本 ----------
import re
def pick_teams(pick):
    out = []
    for part in re.split(r"[+＋]", pick or ""):
        part = part.split("（")[0].split("(")[0]
        for t, z in ZH.items():
            if re.search(rf"(^|\s){re.escape(t)}(\s|$)", part) or (z in part and t in part):
                out.append(t); break
    return out

def read_ledger(path, today):
    rows = []
    if not path: return rows, None
    try:
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
    except Exception:
        return [], None
    ym = today.strftime("%Y-%m")
    used = sum(float(r["stake"] or 0) for r in rows if r.get("tier") == "娛樂" and r.get("date", "").startswith(ym))
    return rows, used

# ---------- 7. HTML ----------
def pct(x, d=0): return "—" if x is None else f"{x*100:.{d}f}%"
def sg(x, d=1): return f"{x:+.{d}f}"
def tname(t): return f"{ZH.get(t, t)} {t}"

CSS = r"""
:root{--bg:#f3f4f1;--panel:#ffffff;--ink:#16202b;--muted:#5d6874;--line:#d9ddd6;--accent:#d2491f;--accent-soft:#fbe7df;--good:#1f7a4d;--bad:#b3261e;--warn:#9a6500;--chip:#eceee9;
--f-disp:"Barlow Condensed","Noto Sans TC",system-ui,sans-serif;--f-body:"Noto Sans TC",system-ui,-apple-system,"PingFang TC",sans-serif;--f-num:"JetBrains Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11161c;--panel:#18202a;--ink:#e8ecef;--muted:#9aa6b2;--line:#2a3542;--accent:#ff7a4d;--accent-soft:#3a2219;--good:#5fd39a;--bad:#ff8a80;--warn:#f0b54a;--chip:#222c38;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11161c;--panel:#18202a;--ink:#e8ecef;--muted:#9aa6b2;--line:#2a3542;--accent:#ff7a4d;--accent-soft:#3a2219;--good:#5fd39a;--bad:#ff8a80;--warn:#f0b54a;--chip:#222c38;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--f-body);font-size:15px;line-height:1.6}
.wrap{max-width:860px;margin:0 auto;padding-block:20px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:22px}
h1{font-family:var(--f-disp);font-weight:700;font-size:2.1rem;letter-spacing:.01em;line-height:1.1;margin:0;text-wrap:balance}
h2{font-family:var(--f-disp);font-weight:600;font-size:1.35rem;letter-spacing:.02em;margin:0 0 10px;text-wrap:balance}
h3{font-size:1rem;margin:0}
.eyebrow{font-family:var(--f-disp);text-transform:uppercase;letter-spacing:.14em;font-size:.8rem;color:var(--accent);font-weight:600}
.meta{color:var(--muted);font-size:.85rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}
.verdict{border-left:4px solid var(--accent);background:var(--accent-soft);border-radius:8px;padding:12px 14px}
.verdict b{font-size:1.05rem}
ol.key{margin:0;padding-left:1.2em;display:grid;gap:6px}
.num{font-family:var(--f-num);font-variant-numeric:tabular-nums}
.pos{color:var(--good)}.neg{color:var(--bad)}.warn{color:var(--warn)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:var(--chip);border-radius:999px;padding:1px 9px;font-size:.78rem;white-space:nowrap}
.chip.hot{background:var(--accent);color:#fff}.chip.bad{background:var(--bad);color:#fff}
.tbl{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:.84rem}
th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:0;background:var(--panel)}
td{padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;font-variant-numeric:tabular-nums}
td.w{white-space:normal;min-width:11rem}
td.r,th.r{text-align:right}
.game{display:grid;gap:12px}
.ghead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.match{font-family:var(--f-disp);font-size:1.35rem;font-weight:600;letter-spacing:.01em}
.prob{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
.prob div{background:var(--chip);border-radius:8px;padding:5px 8px;min-width:0}
.prob span{display:block;font-size:.68rem;color:var(--muted);line-height:1.3}
.prob strong{font-family:var(--f-num);font-size:.98rem;font-weight:600}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;display:flex}
.bar i{display:block;background:var(--accent)}
.cmp td:first-child{color:var(--muted)}
.edge{font-weight:600}
details summary{cursor:pointer;font-weight:500}
details[open] summary{margin-bottom:8px}
.small{font-size:.8rem;color:var(--muted)}
a{color:var(--accent)}
.grid2{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))}
.bet{display:grid;gap:4px;padding:10px 12px;border:1px solid var(--line);border-radius:8px}
.bet .t{font-weight:600}
"""

def ff_rows(g, R):
    h, a = g["home"], g["away"]; H, A = R[h], R[a]
    def row(label, vh, va, fmt, better_high=True, rk_h=None, rk_a=None):
        e = (vh > va) if better_high else (vh < va)
        cls_h = "edge" if e else ""; cls_a = "" if e else "edge"
        rh = f" <span class='small'>#{rk_h}</span>" if rk_h else ""; ra = f" <span class='small'>#{rk_a}</span>" if rk_a else ""
        return f"<tr><td>{label}</td><td class='r num {cls_a}'>{fmt(va)}{ra}</td><td class='r num {cls_h}'>{fmt(vh)}{rh}</td></tr>"
    f1 = lambda v: f"{v:+.1f}"; f0 = lambda v: f"{v:.1f}"; fp = lambda v: f"{v*100:.1f}%"
    rows = [row("調整後淨效率", H["adj"], A["adj"], f1, True, H["adj_rk"], A["adj_rk"]),
            row("進攻效率 ORtg", H["ortg"], A["ortg"], f0, True, H["ortg_rk"], A["ortg_rk"]),
            row("防守效率 DRtg", H["drtg"], A["drtg"], f0, False, H["drtg_rk"], A["drtg_rk"]),
            row("近 5 場淨效率", H["l5net"], A["l5net"], f1, True),
            row("節奏 Pace", H["pace"], A["pace"], f0, True, H["pace_rk"], A["pace_rk"]),
            row("有效命中率 eFG%", H["efg"], A["efg"], fp, True, H["efg_rk"], A["efg_rk"]),
            row("對手 eFG%（防守）", H["oefg"], A["oefg"], fp, False, H["oefg_rk"], A["oefg_rk"]),
            row("失誤率 TOV%", H["tovp"], A["tovp"], fp, False, H["tovp_rk"], A["tovp_rk"]),
            row("進攻籃板率 ORB%", H["orbp"], A["orbp"], fp, True, H["orbp_rk"], A["orbp_rk"]),
            row("罰球率 FT/FGA", H["ftr"], A["ftr"], fp, True, H["ftr_rk"], A["ftr_rk"]),
            row("賽程強度 SOS", H["sos"], A["sos"], f1, True)]
    return "".join(rows)

def why(g, R):
    h, a, f = g["home"], g["away"], g["fav"]; o = a if f == h else h
    bits = []
    d = R[f]["adj"] - R[o]["adj"]
    bits.append(f"{ZH[f]}調整後淨效率高 {d:.1f}" if d > 0 else f"{ZH[f]}數據略遜（{d:+.1f}）但有其他優勢")
    if f == h: bits.append("主場")
    if (g["b2b_a"] and f == h) or (g["b2b_h"] and f == a): bits.append(f"{ZH[o]}背靠背")
    if (g["b2b_h"] and f == h) or (g["b2b_a"] and f == a): bits.append(f"但{ZH[f]}背靠背")
    io = g["inj_a"] if o == a else g["inj_h"]; iff = g["inj_h"] if f == h else g["inj_a"]
    so = [x["name"] for x in io if x["impact"] >= 1.5]; sf = [x["name"] for x in iff if x["impact"] >= 1.5]
    if so: bits.append(f"{ZH[o]}傷兵：{'、'.join(so[:2])}")
    if sf: bits.append(f"注意{ZH[f]}傷兵：{'、'.join(sf[:2])}")
    return "；".join(bits)

def game_card(g, R, tw_odds, pending_map):
    h, a = g["home"], g["away"]; tl = g["tip"].astimezone(TW)
    o = tw_odds.get(g["fav"]); ev = (g["pf"] * o - 1) if o else None
    tags = []
    if g["b2b_a"]: tags.append(f"<span class='chip bad'>{ZH[a]} 背靠背</span>")
    if g["b2b_h"]: tags.append(f"<span class='chip bad'>{ZH[h]} 背靠背</span>")
    tags.append(f"<span class='chip'>{ZH[a]} 休 {max(0,g['gap_a']-1)} 天</span><span class='chip'>{ZH[h]} 休 {max(0,g['gap_h']-1)} 天</span>")
    if g["fav"] in pending_map: tags.append(f"<span class='chip hot'>你已下注：{esc(pending_map[g['fav']])}</span>")
    def injs(lst, t):
        if not lst: return f"<li>{ZH[t]}：無重要傷兵</li>"
        s = "、".join(f"{esc(x['name'])}（{x['status']}，{x['ppg']:.1f} 分，影響 {x['impact']:.1f}）" for x in lst[:4] if x["impact"] > 0.2) or "僅角色球員"
        return f"<li>{ZH[t]}：{s}</li>"
    mkt = f"{g['ml_a']} / {g['ml_h']}" if g["p_mkt"] is not None else "未開盤"
    phome = g["p_home"]; pw = phome * 100
    ev_html = f"<strong class='{'pos' if ev >= 0.05 else 'neg'}'>{ev*100:+.1f}%</strong>" if ev is not None else "<strong>—</strong>"
    return f"""
<article class="card game" id="g{g['id']}">
  <div class="ghead"><span class="match">{ZH[a]} {a} <span class="small">@</span> {ZH[h]} {h}</span><span class="meta num">{tl:%m/%d %H:%M} 台灣</span></div>
  <div class="chips">{''.join(tags)}</div>
  <div>
    <div class="ghead small"><span>{ZH[a]} {pct(1-phome)}</span><span>{ZH[h]} {pct(phome)}</span></div>
    <div class="bar" role="img" aria-label="主隊勝率 {pct(phome)}"><i style="width:{100-pw:.1f}%;background:var(--muted)"></i><i style="width:{pw:.1f}%"></i></div>
  </div>
  <div class="prob">
    <div><span>看好</span><strong>{ZH[g['fav']]}</strong></div>
    <div><span>最終勝率 p</span><strong>{pct(g['pf'])}</strong></div>
    <div><span>市場（DK 去抽水）</span><strong>{pct(g['p_mkt'] if g['fav']==h else (1-g['p_mkt'] if g['p_mkt'] is not None else None))}</strong></div>
    <div><span>模型</span><strong>{pct(g['p_model'] if g['fav']==h else 1-g['p_model'])}</strong></div>
    <div><span>門檻賠率 EV+5%</span><strong>{g['thr']:.2f}</strong></div>
    <div><span>運彩賠率</span><strong>{f'{o:.2f}' if o else '查無'}</strong></div>
    <div><span>EV</span>{ev_html}</div>
    <div><span>預估比分</span><strong>{g['exp_a']:.0f}-{g['exp_h']:.0f}</strong></div>
  </div>
  <p style="margin:0"><b>判讀：</b>{esc(why(g, R))}。預估分差 {ZH[h]} {g['margin']:+.1f}（實力 {g['base']:+.1f}、主場 +{HCA_PTS:.1f}、休息 {g['rest']:+.1f}、傷兵 {g['injadj']:+.1f}）。DK 盤口 {esc(mkt)}{('，讓分 ' + esc(g['spread'])) if g['spread'] else ''}{('，總分 ' + str(g['ou'])) if g['ou'] else ''}；模型總分 {g['exp_a']+g['exp_h']:.0f}。</p>
  <details><summary>四因子與高階數據對照</summary>
    <div class="tbl"><table class="cmp"><thead><tr><th>項目</th><th class="r">{ZH[a]}</th><th class="r">{ZH[h]}</th></tr></thead><tbody>{ff_rows(g, R)}</tbody></table></div>
    <p class="small">粗體是佔優的一方；#數字是聯盟排名。戰績：{ZH[a]} {R[a]['w']}-{R[a]['l']}（客場 {R[a]['away']}，{R[a]['streak']}）／{ZH[h]} {R[h]['w']}-{R[h]['l']}（主場 {R[h]['home']}，{R[h]['streak']}）。</p>
  </details>
  <ul class="small" style="margin:0;padding-left:1.1em">{injs(g['inj_a'], a)}{injs(g['inj_h'], h)}</ul>
</article>"""

def compact_table(G, tw_odds):
    rows = []
    for g in G:
        tl = g["tip"].astimezone(TW); o = tw_odds.get(g["fav"]); ev = g["pf"] * o - 1 if o else None
        flags = []
        if g["b2b_a"]: flags.append(f"{ZH[g['away']]}背靠背")
        if g["b2b_h"]: flags.append(f"{ZH[g['home']]}背靠背")
        st = [x["name"] for x in g["inj_h"] + g["inj_a"] if x["impact"] >= 2]
        if st: flags.append("傷：" + "、".join(st[:2]))
        rows.append(f"<tr><td class='num'>{tl:%m/%d %H:%M}</td><td>{ZH[g['away']]} @ {ZH[g['home']]}</td><td><b>{ZH[g['fav']]}</b></td>"
                    f"<td class='r num'>{pct(g['pf'])}</td><td class='r'>{g['basis']}</td><td class='r num'>{g['thr']:.2f}</td>"
                    f"<td class='r num'>{f'{o:.2f}' if o else '—'}</td><td class='r num'>{f'{ev*100:+.1f}%' if ev is not None else '—'}</td>"
                    f"<td class='r num'>{g['margin'] if g['fav']==g['home'] else -g['margin']:+.1f}</td><td class='w'>{esc('；'.join(flags) or '—')}</td></tr>")
    return ("<div class='tbl'><table><thead><tr><th>時間</th><th>對戰</th><th>看好</th><th class='r'>p</th><th class='r'>依據</th><th class='r'>門檻</th>"
            "<th class='r'>運彩</th><th class='r'>EV</th><th class='r'>預估分差</th><th>注意</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")

# ---------- 8. App 模式（GitHub Pages）：自動新聞、賠率計算機、完整頁面 ----------
import re as _re
def auto_news(news, limit=12):
    out = []
    for a in news.get("articles", []):
        hd = a.get("headline", ""); url = (a.get("links", {}).get("web", {}) or {}).get("href", "")
        if a.get("type") == "Media" or "/video/" in url or "/preview" in url or "fantasy" in url.lower() or "Fantasy" in hd: continue
        low = hd.lower()
        tag = ("交易" if _re.search(r"\btrade", low) else "簽約" if _re.search(r"\bsign|extension|contract", low) else
               "釋出" if _re.search(r"waive|release", low) else
               "傷兵" if _re.search(r"injur|miss|out |strain|sprain|fractur|surgery|hamstring|knee|ankle|calf|\(", low) else
               "異動" if _re.search(r"fire|hire|coach|suspend|fine", low) else "焦點")
        out.append(dict(tag=tag, text=hd, url=url))
        if len(out) >= limit: break
    return out

def app_games(preds):
    G = []
    for k in sorted(preds):
        for g in sorted(preds[k], key=lambda g: g["tip"]):
            h, a = g["home"], g["away"]
            G.append(dict(id=g["id"], tip=g["tip"].astimezone(TW).strftime("%m/%d %H:%M"), day=g["tip"].astimezone(TW).strftime("%m/%d"),
                          home=h, away=a, zh_h=ZH[h], zh_a=ZH[a], p_h=round(g["p_home"], 4), basis=g["basis"],
                          risky_h=bool(risky(g, h)), risky_a=bool(risky(g, a)), b2b_h=g["b2b_h"], b2b_a=g["b2b_a"]))
    return G

CALC_CSS = r"""
.calc{display:grid;gap:10px}
.cg{border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:grid;gap:8px}
.cg .top{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}
.side{display:grid;grid-template-columns:minmax(0,1.3fr) 5.2rem minmax(0,1.6fr);gap:8px;align-items:center}
.side .nm{font-weight:600;min-width:0}
.side .nm small{display:block;font-weight:400;color:var(--muted);font-size:.75rem}
.side input{width:100%;box-sizing:border-box;font:600 1rem var(--f-num);padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.side input:focus{outline:2px solid var(--accent);outline-offset:1px}
.res{font-size:.82rem;line-height:1.4;min-width:0}
.res b{font-family:var(--f-num)}
.tag{display:inline-block;border-radius:6px;padding:0 6px;font-size:.75rem;font-weight:600;color:#fff;background:var(--muted)}
.tag.go{background:var(--good)}.tag.sure{background:var(--accent)}.tag.no{background:var(--bad)}
.pk{display:flex;align-items:center;gap:4px;font-size:.75rem;color:var(--muted)}
.pk input{width:auto}
.parlay{position:sticky;bottom:0;border-top:3px solid var(--accent);z-index:2}
.dtabs{display:flex;gap:6px;flex-wrap:wrap}
.dtabs button{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:3px 12px;cursor:pointer}
.dtabs button[aria-pressed=true]{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn{font:inherit;font-size:.8rem;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:3px 10px;cursor:pointer}
"""

CALC_JS = r"""
(function(){
const G = JSON.parse(document.getElementById('gdata').textContent);
const KEY = 'nba-odds-v1';
let S = {}; try { S = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch(e) { S = {}; }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch(e) {} };
const ceil10 = x => Math.ceil(x / 10) * 10;
const pct = x => (x * 100).toFixed(0) + '%';
function judge(p, o, risky){
  const ev = p * o - 1;
  if (ev >= 0.05){
    const sure = ev >= 0.10 && p >= 0.65 && !risky;
    const target = sure ? 800 : 500;
    let stake = ceil10(target / (o - 1)), net = target;
    if (stake > 500){ stake = 500; net = Math.round(500 * (o - 1)); }
    return {ev, cls: sure ? 'sure' : 'go', label: sure ? '有把握' : '一般', html: `下注 <b>${stake}</b> 元・贏賺約 <b>${net}</b>`};
  }
  return {ev, cls: 'no', label: '不建議', html: `娛樂最多 100 元・贏賺 <b>${Math.round(100*(o-1))}</b>`};
}
const days = [...new Set(G.map(g => g.day))];
let day = days[0];
const root = document.getElementById('calc');
const tabs = document.getElementById('dtabs');
function renderTabs(){
  tabs.innerHTML = days.map(d => `<button type="button" aria-pressed="${d===day}" data-d="${d}">${d}</button>`).join('');
}
tabs.addEventListener('click', e => { const b = e.target.closest('button'); if(!b) return; day = b.dataset.d; renderTabs(); render(); });
function sideRow(g, s){
  const t = s==='h' ? g.home : g.away, zh = s==='h' ? g.zh_h : g.zh_a;
  const p = s==='h' ? g.p_h : 1 - g.p_h, risky = s==='h' ? g.risky_h : g.risky_a;
  const k = g.id + s, o = parseFloat(S[k]);
  let res = `<span class="small">門檻 <b>${(1.05/p).toFixed(2)}</b></span>`;
  if (o > 1){ const j = judge(p, o, risky); res = `<span class="tag ${j.cls}">${j.label}</span> EV <b class="${j.ev>=0.05?'pos':'neg'}">${(j.ev*100>=0?'+':'')+(j.ev*100).toFixed(1)}%</b><br>${j.html}`; }
  return `<div class="side"><div class="nm">${zh} ${t}<small>p ${pct(p)}${risky?'・有風險':''}</small></div>
    <input inputmode="decimal" placeholder="運彩賠率" aria-label="${zh} 運彩賠率" data-k="${k}" value="${S[k]||''}">
    <div class="res" id="r${k}">${res}</div></div>
    <label class="pk"><input type="checkbox" data-pk="${k}" ${S['pk_'+k]?'checked':''}>加入串關</label>`;
}
function render(){
  root.innerHTML = G.filter(g => g.day===day).map(g => `<div class="cg"><div class="top"><span>${g.tip} 台灣・${g.zh_a} @ ${g.zh_h}</span><span>依據：${g.basis}</span></div>${sideRow(g,'a')}${sideRow(g,'h')}</div>`).join('') || '<p>這天沒有比賽。</p>';
  parlay();
}
function find(k){ const g = G.find(x => x.id + 'h' === k || x.id + 'a' === k); const s = k.slice(-1);
  return {g, s, p: s==='h' ? g.p_h : 1-g.p_h, zh: s==='h' ? g.zh_h : g.zh_a}; }
function parlay(){
  const box = document.getElementById('parlay');
  const ks = Object.keys(S).filter(k => k.startsWith('pk_') && S[k]).map(k => k.slice(3)).filter(k => G.some(g => g.id + 'h' === k || g.id + 'a' === k));
  if (!ks.length){ box.hidden = true; return; }
  box.hidden = false;
  let P = 1, O = 1, miss = [];
  const names = ks.map(k => { const f = find(k); const o = parseFloat(S[k]); P *= f.p; if (o > 1) O *= o; else miss.push(f.zh); return `${f.zh}（${pct(f.p)}${o>1?' @ '+o.toFixed(2):''}）`; });
  let out = `<b>串關 ${ks.length} 關：</b>${names.join(' + ')}<br>合併勝率 <b class="num">${pct(P)}</b>`;
  if (miss.length) out += `・請先輸入 ${miss.join('、')} 的賠率`;
  else { const ev = P*O - 1; out += `・合併賠率 <b class="num">${O.toFixed(2)}</b>・EV <b class="num ${ev>=0.05?'pos':'neg'}">${(ev>=0?'+':'')+(ev*100).toFixed(1)}%</b>・${ev>=0.05?'<span class="tag go">值得下</span>':'<span class="tag no">不建議</span>'}`;
    out += `<br><span class="small">串關每多一關，抽水就多疊一層；EV 通常比單買低。</span>`; }
  out += ` <button type="button" class="btn" id="pclear">清除串關</button>`;
  box.innerHTML = out;
  document.getElementById('pclear').onclick = () => { ks.forEach(k => delete S['pk_'+k]); save(); render(); };
}
root.addEventListener('input', e => {
  const k = e.target.dataset.k; if (!k) return;
  S[k] = e.target.value.trim(); save();
  const f = find(k); const o = parseFloat(S[k]); const risky = f.s==='h' ? f.g.risky_h : f.g.risky_a;
  const r = document.getElementById('r'+k);
  if (o > 1){ const j = judge(f.p, o, risky); r.innerHTML = `<span class="tag ${j.cls}">${j.label}</span> EV <b class="${j.ev>=0.05?'pos':'neg'}">${(j.ev*100>=0?'+':'')+(j.ev*100).toFixed(1)}%</b><br>${j.html}`; }
  else r.innerHTML = `<span class="small">門檻 <b>${(1.05/f.p).toFixed(2)}</b></span>`;
  parlay();
});
root.addEventListener('change', e => { const k = e.target.dataset.pk; if (!k) return; if (e.target.checked) S['pk_'+k] = 1; else delete S['pk_'+k]; save(); parlay(); });
renderTabs(); render();
})();
"""

def calc_section(G):
    data = json.dumps(G, ensure_ascii=False).replace("</", "<\\/")
    return (f"<section class='card' id='calcsec'><h2>賠率計算機</h2>"
            f"<p class='small' style='margin-top:-6px'>把運彩 App 上的不讓分賠率填進去，馬上算出 EV、分級和下注金額（規則同你的下注規則）。勾「加入串關」可以算串關。輸入的賠率只存在這支手機。</p>"
            f"<div class='dtabs' id='dtabs' style='margin-bottom:10px'></div><div class='calc' id='calc'></div>"
            f"<div class='card parlay' id='parlay' hidden></div>"
            f"<script type='application/json' id='gdata'>{data}</script><script>{CALC_JS}</script></section>")

HEAD = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#d2491f"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="NBA 情報">
<link rel="manifest" href="manifest.json"><link rel="apple-touch-icon" href="icon-180.png"><link rel="icon" href="icon-192.png">
<style>body{margin:0}</style></head><body>"""

def build(args):
    now_tw = dt.datetime.now(TW); D = now_tw.date()
    # 早上執行（12 點前）時，當天台灣時間的比賽尚未開打 → 以「今天」為逐場分析日
    B = D - dt.timedelta(days=1) if now_tw.hour < 12 else D
    DAYLBL = "今天" if B != D else "明天"
    notes = json.load(open(args.notes, encoding="utf-8")) if args.notes else {}
    tw_odds = {k: float(v) for k, v in (notes.get("tw_odds") or {}).items()}
    events, done, sums, inj, news = fetch_all(D)
    games = sorted([parse_game(e, sums[e["id"]]) for e in done if e["id"] in sums], key=lambda g: g["date"])
    R, lg_ortg, hca100 = team_table(games); P = player_table(games); bt = backtest(games)
    upcoming = sorted([e for e in events.values() if not e["competitions"][0]["status"]["type"]["completed"] and e["season"]["type"] == 2],
                      key=lambda e: e["date"])
    sched = {}
    for e in events.values():
        tip = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        for x in e["competitions"][0]["competitors"]:
            sched.setdefault(x["team"]["abbreviation"], []).append(tip)
    preds = {k: [] for k in (1, 2, 3)}
    for e in upcoming:
        tl = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(TW).date()
        k = (tl - B).days
        if k in preds:
            g = predict(e, R, P, inj, lg_ortg, sched)
            if g: preds[k].append(g)
    # 昨日戰果：台灣時間昨天 06:00 到今天執行為止完賽的比賽
    since = dt.datetime.combine(D if now_tw.hour >= 13 else D - dt.timedelta(days=1), dt.time(0), TW)
    recent = [g for g in games if g["date"] >= since]
    rows_ledger, used = read_ledger(args.ledger, D)
    pending = [r for r in rows_ledger if r.get("result") in ("未派彩", "", None)]
    pending_map = {}
    for r in pending:
        for t in pick_teams(r.get("pick", "")): pending_map.setdefault(t, r["id"])
    D1 = sorted(preds[1], key=lambda g: g["tip"])
    recs = bet_plan(D1, tw_odds)
    fun = sorted([g for g in D1 if not risky(g, g["fav"])], key=lambda g: -g["pf"])[:3]
    budget_left = None if used is None else max(0, 1000 - used)

    # ----- 結論 -----
    key = notes.get("key_points") or []
    if not key:
        top = sorted(D1, key=lambda g: -g["pf"])[:1]
        key = []
        if recent:
            b = max((v for g in recent for v in g["players"]), key=lambda v: v["pts"])
            key.append(f"昨日得分王：{b['name']}（{ZH.get(b['team'], b['team'])}）{b['pts']} 分。")
        if top: key.append(f"{DAYLBL}最穩：{ZH[top[0]['fav']]}，最終勝率 {pct(top[0]['pf'])}，運彩賠率要 ≥ {top[0]['thr']:.2f} 才值得下。")
        if getattr(args, "app", False):
            key.insert(0, "期望值 ≥ +5% 要看運彩賠率：在下方「賠率計算機」輸入運彩賠率，或把截圖傳給 Claude 判斷。")
    bet_html = ""
    if recs:
        items = "".join(f"<div class='bet'><span class='t'>{ZH[r['team']]} {r['team']} 獨贏 @ {r['odds']:.2f}</span><span>{r['tier']}｜下注 {r['stake']} 元｜贏了淨賺約 {r['net']}｜輸了虧 {r['stake']} 元｜勝率 {pct(r['g']['pf'])}｜EV {r['ev']*100:+.1f}%</span></div>" for r in recs)
        bet_html = f"<div class='verdict'><b>有 {len(recs)} 個期望值 ≥ +5% 的注</b></div>{items}"
    else:
        bet_html = "<div class='verdict'><b>今天沒有期望值 ≥ +5% 的注，不建議下注。</b><br><span class='small'>運彩賠率要 ≥ 下表「門檻」才值得下。查不到運彩賠率的場次，請自行比對。</span></div>"
        fi = []
        for g in fun:
            o = tw_odds.get(g["fav"])
            evs = f"{(g['pf']*o-1)*100:+.1f}%" if o else f"{(g['pf']*1.15-1)*100:+.0f}% ～ {(g['pf']*1.25-1)*100:+.0f}%"
            fi.append(f"<li><b>{ZH[g['fav']]} {g['fav']}</b>｜p {pct(g['pf'])}｜運彩 {f'{o:.2f}' if o else '查無'}｜EV {evs}｜{esc(why(g, R))}</li>")
        bl = ("見 Claude 記帳本" if getattr(args, "app", False) else "讀取失敗") if budget_left is None else f"{budget_left:.0f} 元"
        net = "15–25" if not fun or not tw_odds.get(fun[0]["fav"]) else f"{100*(tw_odds[fun[0]['fav']]-1):.0f}"
        bet_html += (f"<div class='card'><h3>🎲 娛樂注（期望值為負，純娛樂）</h3><ol style='margin:.4em 0;padding-left:1.2em'>{''.join(fi)}</ol>"
                     f"<p style='margin:0'>娛樂注｜下注 ≤100 元｜贏了淨賺約 {net} 元｜輸了虧 100 元｜本月娛樂預算剩 {bl}</p>"
                     f"<p class='small' style='margin:.3em 0 0'>挑第一個能單買（最少過關數 1）的。娛樂注長期平均每 100 元約虧 10 元，是買樂趣，不是賺錢。</p></div>")
    # 已下注追蹤
    track = ""
    if pending:
        tr = []
        allg = {g["fav"]: g for k in preds for g in preds[k]}
        allg.update({g["home"]: g for k in preds for g in preds[k]}); allg.update({g["away"]: g for k in preds for g in preds[k]})
        for r in pending:
            ps = []
            for t in pick_teams(r.get("pick", "")):
                if t in allg:
                    g = allg[t]; ps.append(g["p_home"] if t == g["home"] else 1 - g["p_home"])
            p_now = math.prod(ps) if ps else None
            evn = p_now * float(r["odds"]) - 1 if p_now else None
            tr.append(f"<tr><td>{esc(r['id'])}</td><td class='w'>{esc(r['pick'])}</td><td class='r num'>{float(r['odds']):.2f}</td><td class='r num'>{esc(r['stake'])}</td>"
                      f"<td class='r num'>{pct(p_now)}</td><td class='r num {'pos' if (evn or -1) >= 0 else 'neg'}'>{'—' if evn is None else f'{evn*100:+.1f}%'}</td></tr>")
        track = ("<section class='card'><h2>你的未派彩注單</h2><div class='tbl'><table><thead><tr><th>編號</th><th>選項</th><th class='r'>賠率</th><th class='r'>下注</th>"
                 "<th class='r'>最新 p</th><th class='r'>最新 EV</th></tr></thead><tbody>" + "".join(tr) + "</tbody></table></div>"
                 "<p class='small'>最新 p 用今天的盤口與模型重算；串關為兩關相乘。</p></section>")
    # 昨日
    yrows = []; hl = []
    for g in recent:
        h, a = g["home"], g["away"]; H, A = g["H"], g["A"]
        efg = lambda T: (T["fgm"] + 0.5*T["tpm"]) / T["fga"]
        top = {t: max([v for v in g["players"] if v["team"] == t], key=lambda v: v["pts"], default=None) for t in (a, h)}
        yrows.append(f"<tr><td>{ZH[a]} @ {ZH[h]}</td><td class='r num'>{A['pts']}-{H['pts']}{' OT' if g['ot'] else ''}</td><td class='r num'>{g['poss']*48/g['mins']:.0f}</td>"
                     f"<td class='r num'>{efg(A)*100:.0f}% / {efg(H)*100:.0f}%</td><td class='w'>{esc(top[a]['name']) if top[a] else ''} {top[a]['pts'] if top[a] else ''}；{esc(top[h]['name']) if top[h] else ''} {top[h]['pts'] if top[h] else ''}</td></tr>")
        for v in g["players"]:
            dd = sum(x >= 10 for x in (v["pts"], v["reb"], v["ast"], v["stl"], v["blk"]))
            if v["pts"] >= 40 and dd < 3 and not (v["reb"] >= 10 or v["ast"] >= 10): hl.append(f"{v['name']}（{ZH.get(v['team'])}）怒砍 {v['pts']} 分")
            if dd >= 3: hl.append(f"{v['name']}（{ZH.get(v['team'])}）大三元 {v['pts']}/{v['reb']}/{v['ast']}")
            elif v["pts"] >= 30 and (v["reb"] >= 10 or v["ast"] >= 10): hl.append(f"{v['name']}（{ZH.get(v['team'])}）{v['pts']} 分 {v['reb']} 籃板 {v['ast']} 助攻")
    # 傷兵（近 3 天更新、影響 ≥ 1 分）
    injrows = []
    for t in inj.get("injuries", []):
        for i in t["injuries"]:
            nm = i["athlete"]["displayName"]; q = P.get(nm)
            if i.get("date", "")[:10] >= (D - dt.timedelta(days=3)).isoformat() and q and q["impact"] >= 0.8:
                injrows.append((q["impact"], f"<li><b>{ZH.get(q['team'], q['team'])} {esc(nm)}</b>｜{'缺陣' if i['status']=='Out' else '每日觀察'}｜場均 {q['ppg']:.1f} 分｜影響 {q['impact']:.1f}<br><span class='small'>{esc((i.get('shortComment') or '')[:110])}</span></li>"))
    injrows = [r for _, r in sorted(injrows, key=lambda x: -x[0])][:16]
    # 新聞
    newsitems = notes.get("news") or (auto_news(news) if getattr(args, "app", False) else [])
    nlis = "".join(f"<li><span class='chip{' hot' if n.get('tag')=='交易' else ''}'>{esc(n.get('tag','新聞'))}</span> {esc(n['text'])}" + (f" <a href='{esc(n['url'])}' target='_blank' rel='noopener'>來源</a>" if n.get('url') else "") + "</li>" for n in newsitems)
    # 排行
    rk = "".join(f"<tr><td>{i+1}</td><td>{ZH[t]} {t}</td><td class='r num'>{R[t]['w']}-{R[t]['l']}</td><td class='r num'>{R[t]['adj']:+.1f}</td><td class='r num'>{R[t]['net']:+.1f}</td>"
                 f"<td class='r num'>{R[t]['ortg']:.1f}</td><td class='r num'>{R[t]['drtg']:.1f}</td><td class='r num'>{R[t]['pace']:.1f}</td><td class='r num'>{R[t]['efg']*100:.1f}</td>"
                 f"<td class='r num'>{R[t]['tovp']*100:.1f}</td><td class='r num'>{R[t]['orbp']*100:.1f}</td><td class='r num'>{R[t]['ftr']*100:.1f}</td><td class='r num'>{R[t]['l5net']:+.1f}</td><td class='r num'>{R[t]['sos']:+.1f}</td><td>{R[t]['streak']}</td></tr>"
                 for i, t in enumerate(sorted(R, key=lambda t: -R[t]["adj"])))
    srcs = notes.get("sources") or []
    srcs = [dict(t="ESPN 比分／賽程／Box score／DraftKings 盤口 API", u=f"{API}/scoreboard"), dict(t="ESPN 傷兵 API", u=f"{API}/injuries")] + srcs
    d1s = compact_table(sorted(D1, key=lambda g: g["tip"]), tw_odds) if D1 else "<p>台灣時間"+DAYLBL+"沒有比賽。</p>"
    out = f"""<title>NBA 每日情報</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=JetBrains+Mono:wght@500;600&family=Noto+Sans+TC:wght@400;500;700&display=swap">
<style>{CSS}{CALC_CSS if getattr(args, "app", False) else ""}</style>
<main class="wrap">
<header><div class="eyebrow">Julian 的 NBA 情報 · 球探報告</div><h1>{D:%m/%d}（{'一二三四五六日'[D.weekday()]}）NBA 每日情報</h1>
<p class="meta">更新：{now_tw:%Y-%m-%d %H:%M} 台灣時間 · 本季 {len(games)} 場例行賽樣本 · 賽程涵蓋台灣 {(B+dt.timedelta(1)):%m/%d}–{(B+dt.timedelta(3)):%m/%d}</p></header>

<section class="card" id="summary"><h2>結論</h2><ol class="key">{''.join(f'<li>{esc(k)}</li>' for k in key[:3])}</ol></section>

<section id="bets" style="display:grid;gap:10px"><h2 style="margin:0">下注建議</h2>{bet_html}</section>
{calc_section(app_games(preds)) if getattr(args, "app", False) else ""}
{track}
<section class="card" id="d1"><h2>{DAYLBL}（台灣 {(B+dt.timedelta(1)):%m/%d}）賽程預測</h2>{d1s}</section>

<section class="card" id="d23"><h2>台灣 {(B+dt.timedelta(2)):%m/%d}</h2>{compact_table(sorted(preds[2], key=lambda g:g['tip']), tw_odds)}
<h2 style="margin-top:16px">台灣 {(B+dt.timedelta(3)):%m/%d}</h2>{compact_table(sorted(preds[3], key=lambda g:g['tip']), tw_odds)}
<p class="small">「依據」= 市場：以 DraftKings 去抽水為基準，模型最多微調 ±3 個百分點；模型：尚未開盤，只用模型（準確度較低）。預估分差為看好隊領先分數。</p></section>

<section class="card" id="results"><h2>昨日戰果</h2>
{('<p><b>亮點：</b>' + '；'.join(esc(x) for x in hl) + '</p>') if hl else ''}
<div class="tbl"><table><thead><tr><th>對戰</th><th class="r">比分</th><th class="r">節奏</th><th class="r">eFG% 客/主</th><th>雙方得分王</th></tr></thead><tbody>{''.join(yrows) or '<tr><td colspan=5>沒有比賽</td></tr>'}</tbody></table></div></section>

<section class="card" id="news"><h2>交易、異動與新聞</h2><ul style="margin:0;padding-left:1.1em;display:grid;gap:6px">{nlis or '<li>今天沒有重大異動。</li>'}</ul></section>

<section class="card" id="inj"><h2>傷兵動態（近 3 天更新）</h2><ul style="margin:0;padding-left:1.1em;display:grid;gap:8px">{''.join(injrows)}</ul>
<p class="small">影響 = 該球員缺陣時，球隊預估少贏的分數（依 Game Score 估算）。</p></section>

<section class="card" id="ratings"><h2>球隊高階數據排行</h2><div class="tbl"><table><thead><tr><th>#</th><th>球隊</th><th class="r">戰績</th><th class="r">調整淨效率</th><th class="r">淨效率</th><th class="r">ORtg</th><th class="r">DRtg</th><th class="r">Pace</th><th class="r">eFG%</th><th class="r">TOV%</th><th class="r">ORB%</th><th class="r">FT/FGA</th><th class="r">近5淨效率</th><th class="r">SOS</th><th>連勝敗</th></tr></thead><tbody>{rk}</tbody></table></div></section>

<section class="card" id="model"><h2>模型準確度</h2>
<p>逐場回測（每場只用賽前資料預測，共 {bt['n']} 場）：猜中勝方 <b class="num">{bt['acc']*100:.1f}%</b>，Brier 分數 <b class="num">{bt['brier']:.3f}</b>（只猜主隊勝 56% 的基準為 {bt['base_brier']:.3f}，越低越好）。季初樣本少，數字會隨賽季變穩。</p>
<details><summary>方法說明</summary><ul class="small" style="padding-left:1.1em">
<li><b>調整後淨效率</b>：每 100 回合淨勝分，用嶺迴歸扣除對手強弱與主場（本季迴歸主場值 {hca100:+.1f}/100 回合）。</li>
<li><b>實力</b> = 調整淨效率 + 0.15 × 近況差；乘上兩隊平均節奏換算成分差，再加主場 {HCA_PTS} 分、背靠背 −{B2B_PTS} 分、4 天 3 賽 −0.7 分、傷兵影響。</li>
<li><b>傷兵影響</b>：球員場均 Game Score 高於 7 的部分 × 0.35，乘上本季出賽比例（長期缺陣已反映在球隊數據，不重複扣）；成疑算一半。</li>
<li><b>勝率</b>：分差 ÷ {SIGMA} 換成常態機率，再往 50% 收斂 15%。有 DraftKings 盤口時以市場去抽水機率為準，模型只能拉動 ±3 個百分點（市場通常比模型準）。</li>
<li><b>四因子</b>：eFG%（投籃）、TOV%（失誤）、ORB%（進攻籃板）、FT/FGA（罰球），是決定勝負最重要的四項。</li>
<li><b>EV</b> = p × 運彩賠率 − 1；門檻賠率 = 1.05 ÷ p。</li></ul></details></section>

<footer class="small"><p>預測僅供參考，請理性投注、量力而為。</p><p>來源：{'；'.join(f"<a href='{esc(s['u'])}' target='_blank' rel='noopener'>{esc(s['t'])}</a>" for s in srcs)}</p></footer>
</main>"""
    if getattr(args, "app", False): out = HEAD + out + "\n</body></html>"
    open(args.out, "w", encoding="utf-8").write(out)
    summary = dict(date=str(D), recs=[dict(team=r["team"], odds=r["odds"], tier=r["tier"], stake=r["stake"], ev=r["ev"], p=r["g"]["pf"]) for r in recs],
                   fun=[dict(team=g["fav"], p=g["pf"], tw=tw_odds.get(g["fav"])) for g in fun], budget_left=budget_left, backtest=bt,
                   d1=[dict(m=f"{g['away']}@{g['home']}", fav=g["fav"], p=round(g["pf"], 3), p_mkt=g["p_mkt"], p_model=round(g["p_model"], 3), thr=round(g["thr"], 2)) for g in D1],
                   highlights=hl, ngames=len(games))
    if args.json: json.dump(summary, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=1))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="report.html"); ap.add_argument("--notes"); ap.add_argument("--ledger"); ap.add_argument("--json"); ap.add_argument("--app", action="store_true")
    build(ap.parse_args())
