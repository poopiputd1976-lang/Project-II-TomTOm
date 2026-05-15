import streamlit as st
import math
import requests
from datetime import datetime, timedelta
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from folium import plugins
from folium.plugins import FloatImage
from streamlit_folium import st_folium
import pandas as pd
import io
import re

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run Optimization", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางขนส่งนม (VRP Optimization)")
st.markdown("ระบบวิเคราะห์เส้นทางอัจฉริยะ (จัดการข้อมูลราคาน้ำมันด้วยตัวเอง)")

# ==========================================
# 2. ข้อมูลจำลองราคาน้ำมันมาตรฐาน (ใช้เป็นค่าเริ่มต้นเมื่อผู้ใช้เลือก)
# ==========================================
default_oil_prices = {
    "ดีเซลหมุนเร็ว B7 (Premium)": 44.94,
    "ดีเซลหมุนเร็ว B7": 32.94,
    "แก๊สโซฮอล์ 95": 38.55,
    "แก๊สโซฮอล์ E20": 36.44,
    "แก๊สโซฮอล์ 91": 38.18,
    "แก๊สโซฮอล์ E85": 36.14,
    "เบนซิน 95": 46.44
}

# ==========================================
# 3. แผงควบคุมด้านข้าง (Sidebar - Interface ใหม่แยกค่าย/ประเภท/ราคา)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ บริหารจัดการต้นทุนน้ำมัน")
    
    # 1. เลือกค่ายปั๊มน้ำมัน
    station_brand = st.selectbox(
        "🏬 เลือกค่ายปั๊มน้ำมัน",
        options=["PTT Station (ปตท.)", "Bangchak (บางจาก)", "Shell (เชลล์)", "Esso (เอสโซ่)", "PT (พีที)", "อื่นๆ"]
    )
    
    # 2. เลือกประเภทน้ำมัน
    oil_type = st.selectbox(
        "⛽ เลือกประเภทน้ำมัน", 
        options=list(default_oil_prices.keys())
    )
    
    # ดึงค่าเริ่มต้นอ้างอิงมาแสดงก่อน
    initial_price = default_oil_prices[oil_type]
    
    # 3. ช่องกรอกราคาปัจจุบัน (ผู้ใช้สามารถแก้ไขตัวเลขตรงนี้ให้ตรงกับหน้าปั๊มจริงได้เลย)
    THB_L = st.number_input(
        f"💰 ระบุราคาน้ำมันปัจจุบัน (บาท/ลิตร)", 
        min_value=1.0, 
        max_value=100.0,
        value=float(initial_price), 
        step=0.1,
        help="คุณสามารถปรับเปลี่ยนตัวเลขนี้ให้ตรงกับราคาหน้าปั๊มที่คุณเลือกได้ทันที"
    )
    
    st.info(f"📋 ปั๊ม: {station_brand}\n\n⛽ น้ำมัน: {oil_type}\n\n💵 คำนวณที่ราคา: {THB_L:.2f} บาท")

    st.header("📦 พื้นที่บรรทุก")
    KM_L = st.number_input("อัตราสิ้นเปลือง (km/L)", min_value=1.0, value=10.0, step=0.5)
    NUM_COOLERS = st.number_input("จำนวนถัง (ใบ)", min_value=1, value=2)
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=75.0)
    DEAD_SPACE_RATIO = 0.15 

    st.header("🚧 พื้นที่ห้ามผ่าน")
    AVOID_AREA = st.text_area("พิกัดห้ามผ่าน (Lat,Lon:Lat,Lon)", value="")

TOTAL_NET_CAPACITY = int((450 - ICE_PER_COOLER) * NUM_COOLERS)
EMISSION_FACTOR = 2.70757206 

# ==========================================
# 4. จัดการข้อมูลนำเข้า
# ==========================================
st.subheader("📍 นำเข้าข้อมูลจุดจัดส่ง")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel หรือ CSV)", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        df = df.fillna(0)
        edited_df = st.data_editor(df, num_rows="dynamic", height=250, use_container_width=True)
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์ข้อมูลลูกค้าเพื่อเริ่มการวิเคราะห์")
    st.stop()

# --- ฟังก์ชันช่วยคำนวณ ---
def time_to_min(t_str):
    try: h, m = map(int, str(t_str).split(':')); return h * 60 + m
    except: return None 

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))))

# ==========================================
# 5. ประมวลผล (Optimization)
# ==========================================
if st.button("🚀 ประมวลผลเส้นทาง", type="primary", use_container_width=True):
    demands = []
    edited_df = edited_df.reset_index(drop=True)
    
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (float(row.get("200cc", 0)) * 0.2) + (float(row.get("2L", 0)) * 2.0) + (float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    with st.spinner('กำลังคำนวณ...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_idx, to_idx):
            d = dist_matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]
            return int((d/1000)/30*60) + (math.ceil(SERVICE_TIME_SEC/60) if from_idx != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        # จัดการความจุ
        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, [TOTAL_NET_CAPACITY], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        solution = routing.SolveWithParameters(search_params)

    if solution:
        route_indices = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            route_indices.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route_indices.append(0)

        # เรียก TomTom API
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join([f'{coords[n][0]},{coords[n][1]}' for n in route_indices])}/json"
        res = requests.get(url, params={"key": API_KEY, "travelMode": "truck"})
        
        if res.status_code == 200:
            route_data = res.json()['routes'][0]
            summary = route_data['summary']
            dist_km = summary['lengthInMeters'] / 1000
            
            # Dashboard
            c1, c2, c3 = st.columns(3)
            c1.metric("ระยะทางรวม", f"{dist_km:.2f} กม.")
            c2.metric("ต้นทุนน้ำมัน", f"฿{(dist_km / KM_L) * THB_L:.2f}")
            c3.metric("น้ำมันที่ใช้", f"{dist_km / KM_L:.2f} ลิตร")

            # แสดงแผนที่
            m = folium.Map(location=coords[0], zoom_start=12)
            all_points = [[p['latitude'], p['longitude']] for leg in route_data['legs'] for p in leg['points']]
            plugins.AntPath(locations=all_points, color="#2980B9").add_to(m)
            for i, n in enumerate(route_indices[:-1]):
                folium.Marker(coords[n], popup=f"คิวที่ {i}").add_to(m)
            st_folium(m, width="100%", height=500)
        else:
            st.error("ไม่สามารถดึงข้อมูลเส้นทางจาก TomTom ได้ (เช็ค API Key)")
