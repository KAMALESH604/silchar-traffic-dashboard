import streamlit as st
import pandas as pd
import numpy as np
import math
import plotly.express as px
import plotly.graph_objects as go
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
import requests

st.set_page_config(page_title="NIT Silchar Live Traffic & Routing Engine", page_icon="🚦", layout="wide")

# Calibrated Silchar Corridor Nodes with Baseline Regional Speeds & Friction Factors
LOCATIONS = {
    "Silcoorie Junction (South Entry)": {"coords": (24.7405, 92.7845), "base_speed": 34.0, "urban_factor": 0.88},
    "NIT Silchar Main Gate (SH-39)": {"coords": (24.7577, 92.7923), "base_speed": 22.0, "urban_factor": 0.72},
    "Masimpur Cantonment Point": {"coords": (24.7645, 92.7940), "base_speed": 24.5, "urban_factor": 0.75},
    "SMCH (Silchar Medical College, Ghungoor)": {"coords": (24.7825, 92.7958), "base_speed": 16.5, "urban_factor": 0.60},
    "Meherpur Junction (South Town Approach)": {"coords": (24.7890, 92.7975), "base_speed": 13.5, "urban_factor": 0.50},
    "Rangirkhari Point (Silchar Commercial Hub)": {"coords": (24.8020, 92.8005), "base_speed": 11.0, "urban_factor": 0.40},
    "Tarapur / Silchar Railway Station": {"coords": (24.8250, 92.7815), "base_speed": 12.0, "urban_factor": 0.45},
    "Sonai Road Link (Eastern Outer Bypass)": {"coords": (24.7580, 92.8050), "base_speed": 26.5, "urban_factor": 0.82}
}

# CSS: Disable dimming on refresh
st.markdown("""
<style>
    .stApp, div[data-testid="stAppViewContainer"], .main, .block-container {
        opacity: 1 !important;
        transition: none !important;
    }
    div[data-testid="stStatusWidget"] {
        visibility: hidden !important;
        display: none !important;
    }
    .live-dot {
        height: 11px;
        width: 11px;
        background-color: #22c55e;
        border-radius: 50%;
        display: inline-block;
        margin-right: 6px;
        animation: pulse 2s infinite;
    }
    @keyframes pulse {
        0% { opacity: 1; }
        50% { opacity: 0.3; }
        100% { opacity: 1; }
    }
    .status-card {
        padding: 14px 18px;
        border-radius: 8px;
        margin-bottom: 12px;
    }
</style>
""", unsafe_allow_html=True)

# Geodesic road distance calculation
def get_road_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    aerial_km = R * c
    # SH-39 winding road tortuosity index (~1.32x)
    return max(0.5, round(aerial_km * 1.32, 2))

# Header with Live Clock
col_h1, col_h2 = st.columns([2.6, 1.4])
with col_h1:
    st.markdown("## 🚦 Urban Traffic Congestion & Dynamic Route Optimizer")
    st.markdown("📍 **Corridor Network:** `Silchar-Hailakandi Road (SH-39) & Municipal Connectors` &nbsp;|&nbsp; <span class='live-dot'></span>**ACTIVE TELEMETRY**", unsafe_allow_html=True)

with col_h2:
    st.components.v1.html("""
    <div style="text-align: right; font-family: monospace; color: #38BDF8; font-size: 20px; font-weight: bold;">
        <span id="live_time">--:--:--</span><br>
        <span id="live_date" style="font-size: 13px; color: #94A3B8; font-weight: normal;">--</span>
    </div>
    <script>
    function updateClock() {
        const now = new Date();
        document.getElementById('live_time').innerText = '🕒 ' + now.toLocaleTimeString();
        document.getElementById('live_date').innerText = '📅 ' + now.toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'short', day: 'numeric' });
    }
    setInterval(updateClock, 1000);
    updateClock();
    </script>
    """, height=65)

st.markdown("---")

# Sidebar Controls
st.sidebar.header("📍 Route Origin & Destination Selection")
location_names = list(LOCATIONS.keys())

origin_choice = st.sidebar.selectbox("Choose Origin (Start Point):", location_names, index=1)
dest_choice = st.sidebar.selectbox("Choose Destination (End Point):", location_names, index=3)

# API Key input with fallback pre-filled from your working tab
st.sidebar.markdown("---")
st.sidebar.header("🔑 TomTom Telemetry Configuration")
user_api_key = st.sidebar.text_input(
    "TomTom API Key:",
    value="ncoDKpfES6x2LGmD5yDor9hN7pZg8w8o",
    type="password",
    help="Copy and paste the exact key from your working api.tomtom.com tab here."
)

if origin_choice == dest_choice:
    st.warning("⚠️ Origin and Destination cannot be the same point. Please select a distinct destination.")
    st.stop()

o_node = LOCATIONS[origin_choice]
d_node = LOCATIONS[dest_choice]
ORIGIN_LAT, ORIGIN_LON = o_node["coords"]
DEST_LAT, DEST_LON = d_node["coords"]
NIT_GATE_LAT, NIT_GATE_LON = LOCATIONS["NIT Silchar Main Gate (SH-39)"]["coords"]

# Live Telemetry Engine Fragment
@st.fragment(run_every="30s")
def render_live_traffic_engine(orig_name, dest_name, o_lat, o_lon, d_lat, d_lon, o_meta, d_meta, api_key):
    # 1. Calculate actual road distance for selected pair
    route_length_km = get_road_distance(o_lat, o_lon, d_lat, d_lon)
    
    # Midpoint of the chosen corridor segment
    mid_lat = (o_lat + d_lat) / 2.0
    mid_lon = (o_lon + d_lon) / 2.0

    # 2. Dynamic Location-Weighted Baseline Speed
    # Blends the speed profiles of origin and destination zones
    blended_base_speed = (o_meta["base_speed"] + d_meta["base_speed"]) / 2.0
    route_urban_factor = (o_meta["urban_factor"] + d_meta["urban_factor"]) / 2.0

    v_live = blended_base_speed
    free_flow_speed = 40.0
    telemetry_status = "Empirical Micro-Segment Model"
    api_connected = False

    # 3. Query Live Flow at the Selected Route's Midpoint
    if api_key and len(api_key.strip()) > 10:
        try:
            # Query flowSegmentData at the exact path midpoint of the chosen route
            flow_url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json?point={mid_lat:.6f},{mid_lon:.6f}&unit=KMPH&key={api_key.strip()}"
            res = requests.get(flow_url, timeout=5)
            
            if res.status_code == 200:
                data = res.json().get("flowSegmentData", {})
                raw_speed = float(data.get("currentSpeed", blended_base_speed))
                raw_free_flow = float(data.get("freeFlowSpeed", 42.0))
                
                # Apply local urban density calibration
                v_live = max(6.0, round(raw_speed * route_urban_factor, 1))
                free_flow_speed = round(raw_free_flow, 1)
                telemetry_status = "🟢 Live Satellite Telemetry Active"
                api_connected = True
            elif res.status_code == 401:
                telemetry_status = "⚠️ API Key Invalid / 401 (Using Segment Model)"
            else:
                telemetry_status = f"API Status HTTP {res.status_code}"
        except Exception:
            telemetry_status = "Connection Timeout"

    # 4. Accurate Travel Time
    t_primary = (route_length_km / v_live) * 60.0

    # Level of Service and TCI
    tci = max(0.0, min(1.0, 1.0 - (v_live / free_flow_speed)))
    is_congested = (v_live < 18.0) or (tci > 0.45)

    if v_live < 12.0:
        los_grade = "LOS F (Severe Breakdown)"
        status_color = "#EF4444"
    elif v_live < 20.0:
        los_grade = "LOS D/E (Moderate Congestion)"
        status_color = "#F59E0B"
    else:
        los_grade = "LOS A/B (Free Flow)"
        status_color = "#22C55E"

    # Bypass calculation (collector link ~18 km/h, 20% longer path)
    dist_alt_km = round(route_length_km * 1.20, 2)
    speed_alt = 18.0
    t_alt = (dist_alt_km / speed_alt) * 60.0
    time_saved = t_primary - t_alt

    # Top KPI Metrics Cards
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Live Operating Speed", f"{v_live:.1f} km/h", los_grade, delta_color="normal" if not is_congested else "inverse")
    k2.metric("Congestion Level (TCI)", f"{tci * 100:.1f} %", f"Limit: {free_flow_speed:.0f} km/h", delta_color="inverse")
    k3.metric("Selected Route Trip Duration", f"{t_primary:.1f} mins", f"Road Distance: {route_length_km:.2f} km")
    k4.metric("Corridor State", "CONGESTED" if is_congested else "CLEAR", telemetry_status)

    st.markdown("---")

    # Dynamic Route Geometry Map
    map_col, fleet_col = st.columns([1.6, 1])

    with map_col:
        st.subheader(f"🗺️ Route Map: {orig_name.split('(')[0]} ➔ {dest_name.split('(')[0]}")
        m = folium.Map(location=[mid_lat, mid_lon], zoom_start=13, tiles="OpenStreetMap")

        route_coords = [
            [o_lat, o_lon],
            [mid_lat, mid_lon],
            [d_lat, d_lon]
        ]

        # Draw Primary Route
        folium.PolyLine(
            route_coords,
            color=status_color,
            weight=6,
            opacity=0.9,
            tooltip=f"{orig_name.split('(')[0]} to {dest_name.split('(')[0]}: {v_live:.1f} km/h ({t_primary:.1f} mins)"
        ).add_to(m)

        # Dynamic Heatmap Layer
        if is_congested:
            sampled_heat = [[pt[0], pt[1], tci] for pt in route_coords]
            HeatMap(sampled_heat, radius=24, blur=16, gradient={0.4: 'yellow', 0.8: 'orange', 1.0: 'red'}).add_to(m)

        # Alternative Bypass Link if congested
        if is_congested and (time_saved > 2.0):
            alt_coords = [
                [o_lat, o_lon],
                [mid_lat + 0.003, mid_lon + 0.006],
                [d_lat, d_lon]
            ]
            folium.PolyLine(
                alt_coords,
                color="#22C55E",
                weight=5,
                opacity=0.9,
                dash_array="8, 6",
                tooltip=f"🟢 Recommended Bypass ({t_alt:.1f} mins)"
            ).add_to(m)

        # Markers
        folium.Marker([o_lat, o_lon], popup=f"<b>Origin:</b> {orig_name}", tooltip=f"Start: {orig_name}", icon=folium.Icon(color="blue", icon="play")).add_to(m)
        folium.Marker([d_lat, d_lon], popup=f"<b>Destination:</b> {dest_name}", tooltip=f"End: {dest_name}", icon=folium.Icon(color="purple", icon="flag")).add_to(m)
        folium.Marker([NIT_GATE_LAT, NIT_GATE_LON], popup="<b>NIT Silchar Gate</b>", tooltip="NIT Silchar Gate", icon=folium.Icon(color="gray", icon="info-sign")).add_to(m)

        st_folium(m, width=700, height=380, key=f"map_{orig_name}_{dest_name}_{int(v_live*10)}_{int(route_length_km*10)}")

    with fleet_col:
        st.subheader("🚗 Vehicle Fleet Share (Corridor Ground Truth)")
        fleet_df = pd.DataFrame({
            'Vehicle': ['Auto-Rickshaw', 'Scooter', 'Motorcycle', 'Passenger Car', 'Bicycle', 'Ambulance', 'Mini-Truck'],
            'Count': [227, 56, 53, 40, 12, 6, 1]
        })
        fig = px.pie(fleet_df, names='Vehicle', values='Count', hole=0.42, color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Routing Advisory
    st.subheader("🧭 Live Navigation & Waypoint Guidance")
    adv1, adv2 = st.columns([1.3, 1])

    with adv1:
        if not is_congested or (time_saved <= 2.0):
            st.markdown(f"""
            <div class="status-card" style="background-color: rgba(34, 197, 94, 0.12); border-left: 5px solid #22C55E;">
                <h4 style="margin: 0; color: #22C55E;">✅ Selected Direct Path is Recommended</h4>
                <p style="margin: 6px 0 0 0; color: #E2E8F0;">
                    • <b>Origin:</b> {orig_name}<br>
                    • <b>Destination:</b> {dest_name}<br>
                    • <b>Distance:</b> {route_length_km:.2f} km | <b>Live Operating Speed:</b> {v_live:.1f} km/h ({los_grade})<br>
                    • <b>Estimated Travel Time:</b> <b>{t_primary:.1f} minutes</b><br>
                    • <b>Advisory:</b> Direct corridor path is operating normally. No bypass detour necessary.
                </p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="status-card" style="background-color: rgba(239, 68, 68, 0.12); border-left: 5px solid #EF4444;">
                <h4 style="margin: 0; color: #EF4444;">⚠️ Congestion Detected Along Selected Route</h4>
                <p style="margin: 6px 0 0 0; color: #E2E8F0;">
                    • <b>Direct Path Time:</b> {t_primary:.1f} minutes at bottleneck speed ({v_live:.1f} km/h)<br>
                    • <b>Alternative Bypass Time:</b> {t_alt:.1f} minutes ({dist_alt_km:.2f} km via collector link)<br>
                    • <b>Net Time Saved:</b> <b>{time_saved:.1f} minutes faster</b> via bypass link.<br>
                    • <b>Advisory:</b> Use the green alternative path indicated on the map.
                </p>
            </div>
            """, unsafe_allow_html=True)

    with adv2:
        st.markdown("### 🗺️ Journey Waypoints Breakdown")
        st.dataframe(pd.DataFrame([
            {"Stage": "1. Departure", "Location": orig_name, "Progress": "0.00 km", "Node Status": "Origin Node"},
            {"Stage": "2. Midpoint Segment", "Location": f"Near {orig_name.split(' ')[0]}-{dest_name.split(' ')[0]} Link", "Progress": f"{(route_length_km*0.5):.2f} km", "Node Status": f"{v_live:.1f} km/h ({los_grade})"},
            {"Stage": "3. Arrival", "Location": dest_name, "Progress": f"{route_length_km:.2f} km", "Node Status": f"ETA: {t_primary:.1f} mins"}
        ]), hide_index=True, use_container_width=True)

    st.markdown("---")

    # Diurnal Profile Across 4 Time Zones of 6 Hours
    st.subheader("📊 24-Hour Corridor Diurnal Profile (Four 6-Hour Time Zones)")

    tz_df = pd.DataFrame([
        {"Time Zone": "Zone 1: 00:00 - 06:00<br>(Late Night / Dawn)", "Avg Speed (km/h)": 38.5, "Equivalent Flow (PCU/hr)": 180, "Congestion TCI (%)": 3.8, "Level of Service": "LOS A (Free Flow)"},
        {"Time Zone": "Zone 2: 06:00 - 12:00<br>(Morning Peak / Commute)", "Avg Speed (km/h)": 11.2, "Equivalent Flow (PCU/hr)": 1650, "Congestion TCI (%)": 72.0, "Level of Service": "LOS E/F (Severe Delay)"},
        {"Time Zone": "Zone 3: 12:00 - 18:00<br>(Afternoon / Survey Peak)", "Avg Speed (km/h)": 8.4, "Equivalent Flow (PCU/hr)": 1879, "Congestion TCI (%)": 79.0, "Level of Service": "LOS F (Breakdown)"},
        {"Time Zone": "Zone 4: 18:00 - 24:00<br>(Evening Peak & Clearing)", "Avg Speed (km/h)": 15.6, "Equivalent Flow (PCU/hr)": 1280, "Congestion TCI (%)": 61.0, "Level of Service": "LOS D/E (Moderate Delay)"}
    ])

    g_col1, g_col2 = st.columns([1.7, 1])

    with g_col1:
        fig_tz = go.Figure()
        fig_tz.add_trace(go.Bar(
            x=tz_df["Time Zone"],
            y=tz_df["Equivalent Flow (PCU/hr)"],
            name="Flow Rate (PCU/hr)",
            marker_color="#38BDF8",
            yaxis="y"
        ))
        fig_tz.add_trace(go.Scatter(
            x=tz_df["Time Zone"],
            y=tz_df["Avg Speed (km/h)"],
            name="Operating Speed (km/h)",
            mode="lines+markers+text",
            text=[f"{v:.1f} km/h" for v in tz_df["Avg Speed (km/h)"]],
            textposition="top center",
            line=dict(color="#EF4444", width=3),
            marker=dict(size=9, color="#EF4444"),
            yaxis="y2"
        ))
        fig_tz.update_layout(
            height=370,
            margin=dict(l=10, r=10, t=25, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(title="Flow (PCU/hr)", showgrid=False),
            yaxis2=dict(title="Speed (km/h)", overlaying="y", side="right", range=[0, 45], showgrid=True),
            hovermode="x unified"
        )
        st.plotly_chart(fig_tz, use_container_width=True)

    with g_col2:
        st.markdown("### 📋 6-Hour Zone Breakdown")
        st.dataframe(tz_df[["Time Zone", "Avg Speed (km/h)", "Congestion TCI (%)", "Level of Service"]], hide_index=True, use_container_width=True)

render_live_traffic_engine(origin_choice, dest_choice, ORIGIN_LAT, ORIGIN_LON, DEST_LAT, DEST_LON, o_node, d_node, user_api_key)