import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(page_title="SH-39 Live Traffic Command Centre", page_icon="🚦", layout="wide")

IST = ZoneInfo("Asia/Kolkata")
V_FREE = 40.0  # free-flow speed adopted in the project report (km/h)
PLACES = {
    "Silcoorie Junction (South Entry)": (24.7405, 92.7845),
    "NIT Silchar Main Gate (SH-39)": (24.7577, 92.7923),
    "Masimpur Cantonment Point": (24.7645, 92.7940),
    "SMCH (Silchar Medical College, Ghungoor)": (24.7825, 92.7958),
    "Meherpur Junction (South Town Approach)": (24.7890, 92.7975),
    "Rangirkhari Point (Silchar Commercial Hub)": (24.8020, 92.8005),
    "Tarapur / Silchar Railway Station": (24.8250, 92.7815),
    "Sonai Road Link (Eastern Outer Bypass)": (24.7580, 92.8050),
}
NAMES = list(PLACES)

st.markdown(
    """
<style>
    .block-container {padding-top: 1.3rem; max-width: 1450px;}
    div[data-testid="stStatusWidget"] {display: none !important;}
    .stApp, .main, .block-container {opacity: 1 !important; transition: none !important;}
    div[data-testid="stMetric"] {background: rgba(148,163,184,.09); padding: 14px 16px;
        border-radius: 12px; border: 1px solid rgba(148,163,184,.2);}
    .hero {padding: 20px 28px; border-radius: 16px; margin-bottom: 12px;
        background: linear-gradient(120deg, #0F1B2D 0%, #1B2B45 100%); border: 1px solid #2A3B57;}
    .hero h1 {margin: 0; font-size: 1.8rem; color: #F4F1EA;}
    .hero p {margin: 6px 0 0 0; color: #B8C2D0;}
    .pill {display: inline-block; padding: 3px 12px; border-radius: 999px; font-weight: 700; color: #fff;}
    .dot {height: 11px; width: 11px; border-radius: 50%; display: inline-block; margin-right: 6px;}
    .live {background: #22c55e; animation: pulse 1.6s infinite;} .off {background: #f59e0b;}
    @keyframes pulse {0% {opacity: 1;} 50% {opacity: .25;} 100% {opacity: 1;}}
    .card {padding: 14px 18px; border-radius: 10px; margin-bottom: 10px; border-left: 6px solid;}
</style>
""",
    unsafe_allow_html=True,
)
ss = st.session_state


# ----------------------------------------------------------------------------- helpers
def api_key():
    try:
        return str(st.secrets["TOMTOM_API_KEY"]).strip()
    except Exception:
        return os.environ.get("TOMTOM_API_KEY", "").strip()


def los(v):
    if v > 35: return "A", "Free flow", "#22C55E"
    if v > 28: return "B", "Stable flow", "#22C55E"
    if v > 20: return "C", "Restricted movement", "#F59E0B"
    if v > 15: return "D", "Approaching unstable", "#F59E0B"
    if v > 10: return "E", "Unstable, at capacity", "#EF4444"
    return "F", "Forced stop-and-go", "#EF4444"


def ratio_color(r):
    return "#22C55E" if r > 0.75 else "#F59E0B" if r > 0.45 else "#EF4444"


def short(n):
    return n.split(" (")[0]


def now_ist():
    return datetime.now(IST)


def haversine_km(a, b):
    r = 6371.0
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(h), math.sqrt(1 - h))


# ----------------------------------------------------------------------------- TomTom data
@st.cache_data(ttl=25, show_spinner=False)
def fetch_flow(key, lat, lon):
    """Current and free-flow speed on the road segment nearest a point."""
    if not key:
        return None
    try:
        res = requests.get(
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
            params={"point": f"{lat:.6f},{lon:.6f}", "unit": "KMPH", "key": key}, timeout=6)
        if res.status_code == 200:
            d = res.json()["flowSegmentData"]
            return {"cur": float(d["currentSpeed"]), "free": float(d["freeFlowSpeed"]),
                    "closed": bool(d.get("roadClosure", False))}
    except Exception:
        pass
    return None


@st.cache_data(ttl=25, show_spinner=False)
def fetch_routes(key, o, d):
    """Best route and alternatives with live traffic delay."""
    if not key:
        return None
    try:
        res = requests.get(
            f"https://api.tomtom.com/routing/1/calculateRoute/{o[0]},{o[1]}:{d[0]},{d[1]}/json",
            params={"key": key, "traffic": "true", "travelMode": "car", "routeType": "fastest", "maxAlternatives": 2,
                    "computeTravelTimeFor": "all", "instructionsType": "text", "language": "en-GB"}, timeout=8)
        if res.status_code != 200:
            return None
        out = []
        for r in res.json().get("routes", []):
            s = r["summary"]
            steps = [{"Instruction": i["message"], "From start (km)": round(i.get("routeOffsetInMeters", 0) / 1000, 2)}
                     for i in r.get("guidance", {}).get("instructions", []) if i.get("message")]
            out.append({
                "km": s["lengthInMeters"] / 1000, "sec": s["travelTimeInSeconds"],
                "delay": s.get("trafficDelayInSeconds", 0),
                "free": s.get("noTrafficTravelTimeInSeconds", s["travelTimeInSeconds"]),
                "pts": [(p["latitude"], p["longitude"]) for leg in r["legs"] for p in leg["points"]], "steps": steps})
        return out or None
    except Exception:
        return None


def flows_for(key, pts):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(lambda p: fetch_flow(key, *p), pts))


KEY = api_key()


@st.cache_data(ttl=60, show_spinner=False)
def check_key(key):
    """Plain-language test of each TomTom service the dashboard uses."""
    if not key:
        return [("Key", False, "No key found. Add TOMTOM_API_KEY in Streamlit Settings > Secrets, then reboot.")]
    tests = [
        ("Traffic API", "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
         {"point": "24.7577,92.7923", "unit": "KMPH", "key": key}),
        ("Routing API", "https://api.tomtom.com/routing/1/calculateRoute/24.7577,92.7923:24.7825,92.7958/json",
         {"key": key, "traffic": "true"}),
    ]
    out = []
    for name, url, params in tests:
        try:
            r = requests.get(url, params=params, timeout=8)
            c = r.status_code
            if c == 200:
                out.append((name, True, "working"))
            elif c in (401, 403):
                out.append((name, False, f"key rejected (HTTP {c}). Re-copy the key and tick {name} for it in the TomTom dashboard."))
            elif c == 429:
                out.append((name, False, "daily limit reached (HTTP 429)"))
            else:
                out.append((name, False, f"HTTP {c}: {r.text[:100]}"))
        except Exception as e:
            out.append((name, False, f"could not reach TomTom ({type(e).__name__})"))
    return out


st.sidebar.header("🔌 Connection")
for _n, _ok, _msg in check_key(KEY):
    st.sidebar.markdown(f"{'✅' if _ok else '❌'} **{_n}:** {_msg}")
if KEY:
    st.sidebar.caption(f"Key loaded: {len(KEY)} characters, ending ...{KEY[-4:]}")

# ----------------------------------------------------------------------------- header
h1, h2 = st.columns([3, 1])
with h1:
    st.markdown("<div class='hero'><h1>🚦 SH-39 Live Traffic Command Centre</h1>"
                "<p>Live corridor speeds · congestion grade · fastest route right now · NIT Silchar Data Analytics Project</p></div>",
                unsafe_allow_html=True)
with h2:
    st.components.v1.html("""<div style="text-align:right;font-family:monospace;color:#38BDF8;font-size:22px;font-weight:bold;padding-top:14px">
    <span id="t">--:--:--</span><br><span id="d" style="font-size:13px;color:#94A3B8;font-weight:normal">--</span></div>
    <script>function u(){const n=new Date();document.getElementById('t').innerText=n.toLocaleTimeString();
    document.getElementById('d').innerText=n.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'short',year:'numeric'});}
    setInterval(u,1000);u();</script>""", height=90)

tab_live, tab_route, tab_survey = st.tabs(["📡 Live Corridor Monitor", "🧭 Route Planner", "📊 Survey Baseline"])


# ----------------------------------------------------------------------------- live monitor
@st.fragment(run_every="30s")
def live_monitor():
    flows = flows_for(KEY, list(PLACES.values()))
    ok = [(n, f) for n, f in zip(NAMES, flows) if f]
    if not ok:
        st.markdown("<span class='dot off'></span>**Live traffic is temporarily unavailable.** It will reconnect automatically.",
                    unsafe_allow_html=True)
        return
    st.markdown(f"<span class='dot live'></span>**LIVE TRAFFIC** &nbsp;·&nbsp; {len(ok)} of {len(NAMES)} points reporting "
                f"&nbsp;·&nbsp; updated {now_ist().strftime('%H:%M:%S')} IST", unsafe_allow_html=True)

    df = pd.DataFrame([{"Location": short(n), "Live speed": f["cur"], "Free flow": f["free"],
                        "Ratio": f["cur"] / max(f["free"], 1), "Closed": f["closed"]} for n, f in ok])
    avg = df["Live speed"].mean()
    tci = max(0.0, 1 - avg / V_FREE)
    g, label, color = los(avg)
    slow, fast = df.loc[df["Live speed"].idxmin()], df.loc[df["Live speed"].idxmax()]
    jammed = int((df.Ratio < 0.45).sum())
    ss.hist = (ss.get("hist", []) + [{"t": now_ist().replace(tzinfo=None), "speed": avg, "tci": tci * 100}])[-240:]

    if g in ("E", "F"):
        st.markdown(f"<div class='card' style='background:{color}22;border-color:{color}'><b style='color:{color}'>"
                    f"🔴 LOS {g}: {label}.</b> Corridor average {avg:.1f} km/h. Slowest point: {slow.Location} ({slow['Live speed']:.0f} km/h).</div>",
                    unsafe_allow_html=True)
    for _, r in df[df.Closed].iterrows():
        st.error(f"⛔ Road closure reported near {r.Location}.")

    c = st.columns(5)
    c[0].metric("Corridor speed", f"{avg:.1f} km/h")
    c[1].metric("Congestion (TCI)", f"{tci * 100:.0f} %")
    c[2].metric("Slowest point", f"{slow['Live speed']:.0f} km/h", slow.Location, delta_color="off")
    c[3].metric("Fastest point", f"{fast['Live speed']:.0f} km/h", fast.Location, delta_color="off")
    c[4].metric("Points congested", f"{jammed} / {len(df)}")
    st.markdown(f"**Level of Service:** <span class='pill' style='background:{color}'>LOS {g}</span> {label}", unsafe_allow_html=True)

    a, b = st.columns([1.3, 1])
    with a:
        mp = folium.Map(location=[24.785, 92.795], zoom_start=12, tiles="OpenStreetMap")
        for n, f in ok:
            r = f["cur"] / max(f["free"], 1)
            folium.CircleMarker(PLACES[n], radius=14, color=ratio_color(r), fill=True, fill_opacity=0.85,
                                tooltip=f"{short(n)}: {f['cur']:.0f} km/h (free flow {f['free']:.0f})").add_to(mp)
        st_folium(mp, height=420, use_container_width=True, returned_objects=[], key="monitor_map")
        st.caption("Green = flowing, amber = slow, red = congested (compared with free-flow speed).")
    with b:
        fig = go.Figure()
        fig.add_bar(y=df.Location, x=df["Free flow"], name="Free flow", orientation="h", marker_color="#475569")
        fig.add_bar(y=df.Location, x=df["Live speed"], name="Live speed", orientation="h", marker_color=[ratio_color(r) for r in df.Ratio])
        fig.update_layout(barmode="group", height=420, title="Live speed by location (km/h)", margin=dict(t=50, b=10, l=10, r=10),
                          legend=dict(orientation="h", y=1.1), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)

    h = pd.DataFrame(ss.hist)
    if len(h) > 1:
        fig = go.Figure()
        fig.add_hrect(y0=0, y1=10, fillcolor="#EF4444", opacity=0.12, line_width=0)
        fig.add_hrect(y0=10, y1=20, fillcolor="#F59E0B", opacity=0.10, line_width=0)
        fig.add_hrect(y0=20, y1=50, fillcolor="#22C55E", opacity=0.08, line_width=0)
        fig.add_scatter(x=h.t, y=h.speed, mode="lines+markers", line=dict(color="#38BDF8", width=3), name="Corridor speed")
        fig.update_layout(height=280, title="Corridor speed since you opened this page (km/h)", margin=dict(t=50, b=10),
                          yaxis=dict(range=[0, max(45, h.speed.max() + 3)]), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)


with tab_live:
    live_monitor()


# ----------------------------------------------------------------------------- route planner
@st.fragment(run_every="30s")
def route_planner():
    ss.setdefault("o", NAMES[1])
    ss.setdefault("d", NAMES[3])
    c1, c2, c3 = st.columns([3, 3, 1.2])
    c1.selectbox("From", NAMES, key="o")
    c2.selectbox("To", NAMES, key="d")
    c3.button("⇅ Swap", on_click=lambda: ss.update(o=ss.d, d=ss.o), use_container_width=True)
    o_name, d_name = ss.o, ss.d
    if o_name == d_name:
        st.warning("Choose two different places.")
        return
    o, d = PLACES[o_name], PLACES[d_name]
    routes = fetch_routes(KEY, o, d)
    live = routes is not None
    if not live:
        km = haversine_km(o, d) * 1.32
        routes = [{"km": km, "sec": km / 18 * 3600, "delay": 0, "free": km / V_FREE * 3600, "pts": [o, d], "steps": []}]
    routes.sort(key=lambda r: r["sec"])
    best = routes[0]
    v = best["km"] / (best["sec"] / 3600)
    g, label, color = los(v)
    delay = best["delay"] / 60
    arrive = now_ist() + timedelta(seconds=best["sec"])

    if live:
        st.markdown(f"<span class='dot live'></span>**LIVE TRAFFIC** · updated {now_ist().strftime('%H:%M:%S')} IST", unsafe_allow_html=True)
    else:
        st.markdown("<span class='dot off'></span>**Estimate only.** Live traffic is temporarily unavailable.", unsafe_allow_html=True)

    m = st.columns(4)
    m[0].metric("Arrive by", arrive.strftime("%I:%M %p"), f"{best['km']:.1f} km by road", delta_color="off")
    m[1].metric("Travel time now", f"{best['sec'] / 60:.0f} min", f"{delay:+.1f} min traffic delay" if live else None, delta_color="inverse")
    m[2].metric("Average route speed", f"{v:.1f} km/h", f"LOS {g}: {label}", delta_color="off")
    m[3].metric("Free-flow time", f"{best['free'] / 60:.0f} min", "with no traffic", delta_color="off")

    idx = list(range(len(best["pts"]))) if len(best["pts"]) <= 8 else [round(i * (len(best["pts"]) - 1) / 7) for i in range(8)]
    flows = flows_for(KEY, [best["pts"][i] for i in idx]) if live else []

    a, b = st.columns([1.7, 1])
    with a:
        mp = folium.Map(location=[(o[0] + d[0]) / 2, (o[1] + d[1]) / 2], zoom_start=13, tiles="OpenStreetMap")
        for r in routes[1:]:
            folium.PolyLine(r["pts"], color="#94A3B8", weight=5, opacity=0.8, dash_array="8,8",
                            tooltip=f"Alternative: {r['km']:.1f} km, {r['sec'] / 60:.0f} min").add_to(mp)
        if sum(1 for f in flows if f) >= 2:
            for k in range(len(idx) - 1):
                f = flows[k] or flows[k + 1]
                seg = best["pts"][idx[k]: idx[k + 1] + 1]
                folium.PolyLine(seg, color=ratio_color(f["cur"] / max(f["free"], 1)) if f else color, weight=8, opacity=0.95,
                                tooltip=f"{f['cur']:.0f} km/h" if f else "no data").add_to(mp)
        else:
            folium.PolyLine(best["pts"], color=color, weight=8, opacity=0.95).add_to(mp)
        folium.Marker(o, tooltip=f"Start: {o_name}", icon=folium.Icon(color="blue", icon="play")).add_to(mp)
        folium.Marker(d, tooltip=f"End: {d_name}", icon=folium.Icon(color="red", icon="flag")).add_to(mp)
        mp.fit_bounds(best["pts"] if len(best["pts"]) > 2 else [o, d], padding=(30, 30))
        st_folium(mp, height=470, use_container_width=True, returned_objects=[], key=f"rmap_{o_name}_{d_name}")
        st.caption("Route colour: green = flowing, amber = slow, red = congested. Dashed grey = alternative road.")
    with b:
        tone = "#22C55E" if delay < 2 else "#F59E0B" if delay < 6 else "#EF4444"
        head = "Clear road ahead" if delay < 2 else "Moderate delay" if delay < 6 else "Heavy congestion"
        msg = f"Leave now and expect <b>{best['sec'] / 60:.0f} min</b>"
        msg += f" (about {delay:.0f} min of traffic delay)." if live else "."
        if len(routes) > 1:
            msg += f" Quickest of {len(routes)} roads, {(routes[1]['sec'] - best['sec']) / 60:.1f} min ahead of the next."
        st.markdown(f"<div class='card' style='background:{tone}22;border-color:{tone}'><h4 style='color:{tone};margin:0'>{head}</h4>"
                    f"<p style='margin:6px 0 0 0'><b>{short(o_name)}</b> → <b>{short(d_name)}</b><br>{msg}</p></div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"Route": "★ Fastest" if i == 0 else f"Alternative {i}", "Distance": f"{r['km']:.1f} km",
                                    "Time now": f"{r['sec'] / 60:.0f} min",
                                    "vs fastest": "-" if i == 0 else f"+{(r['sec'] - best['sec']) / 60:.1f} min"}
                                   for i, r in enumerate(routes)]), hide_index=True)
        pf = [(i, f) for i, f in zip(idx, flows) if f]
        if pf:
            xs = [round(best["km"] * i / max(len(best["pts"]) - 1, 1), 2) for i, _ in pf]
            fig = go.Figure()
            fig.add_bar(x=xs, y=[f["free"] for _, f in pf], name="Free flow", marker_color="#475569")
            fig.add_bar(x=xs, y=[f["cur"] for _, f in pf], name="Live speed",
                        marker_color=[ratio_color(f["cur"] / max(f["free"], 1)) for _, f in pf])
            fig.update_layout(barmode="group", height=250, margin=dict(l=5, r=5, t=40, b=5),
                              title=dict(text="Live speed along your route (km/h)", font=dict(size=14)),
                              xaxis_title="Distance from start (km)", legend=dict(orientation="h", y=1.2))
            st.plotly_chart(fig, use_container_width=True)
    if best["steps"]:
        with st.expander("🛣️ Turn-by-turn directions for the fastest route"):
            st.dataframe(pd.DataFrame(best["steps"]), hide_index=True)


with tab_route:
    route_planner()

# ----------------------------------------------------------------------------- survey baseline
with tab_survey:
    st.markdown("### Video survey of the SMCH–NIT section (SH-39)")
    st.caption("395 vehicles classified from 25 fps video over about 12.2 minutes. Use these as the baseline to compare with live speeds.")
    s = st.columns(6)
    for col, (lab, val) in zip(s, [("Vehicles", "395"), ("Mean speed", "6.30 km/h"), ("Flow", "1,946 veh/hr"),
                                   ("PCU flow", "1,879 /hr"), ("TCI", "84.2 %"), ("Level of Service", "F")]):
        col.metric(lab, val)
    veh = pd.DataFrame({"Vehicle": ["Auto-rickshaw", "Scooter", "Motorcycle", "Car", "Bicycle", "Ambulance", "Mini truck"],
                        "Count": [227, 56, 53, 40, 12, 6, 1], "PCU": [272.4, 28.0, 26.5, 40.0, 4.8, 8.4, 1.4]})
    a, b = st.columns(2)
    with a:
        fig = px.pie(veh, names="Vehicle", values="Count", hole=0.45, title="Vehicle composition",
                     color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_layout(height=370, margin=dict(t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with b:
        fig = go.Figure()
        fig.add_bar(x=veh.Vehicle, y=veh.Count, name="Vehicles", marker_color="#38BDF8")
        fig.add_bar(x=veh.Vehicle, y=veh.PCU, name="PCU", marker_color="#F59E0B")
        fig.update_layout(barmode="group", height=370, title="Count versus PCU", margin=dict(t=50, b=10), legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, use_container_width=True)
    t = pd.DataFrame({"Interval": ["0–5 min", "5–10 min", "10–12.3 min"], "Vehicles": [132, 175, 88], "Avg speed (km/h)": [5.88, 6.34, 6.82]})
    a, b = st.columns([1.5, 1])
    with a:
        fig = go.Figure()
        fig.add_bar(x=t.Interval, y=t.Vehicles, name="Vehicles", marker_color="#38BDF8")
        fig.add_scatter(x=t.Interval, y=t["Avg speed (km/h)"], name="Avg speed", yaxis="y2", mode="lines+markers",
                        line=dict(color="#EF4444", width=3))
        fig.update_layout(height=330, title="Volume and speed by interval", margin=dict(t=50, b=10),
                          yaxis2=dict(overlaying="y", side="right", range=[0, 20], title="km/h"), legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, use_container_width=True)
    with b:
        st.dataframe(pd.DataFrame({"LOS": list("ABCDEF"), "Speed (km/h)": ["> 35", "28–35", "20–28", "15–20", "10–15", "< 10"],
                                   "Condition": ["Free flow", "Stable", "Restricted", "Near unstable", "At capacity", "Forced flow"]}),
                     hide_index=True)
        st.error("Surveyed section: LOS F, with 90.1% of vehicles below 10 km/h.")

st.caption("Live data: TomTom Traffic Flow and Routing APIs · Survey baseline: SH-39 video study, NIT Silchar (CE481).")
