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
st.markdown("ระบบวิเคราะห์เส้นทางอัจฉริยะ พร้อมราคาน้ำมันดึงสด Realtime ไฮบริด")

# ==========================================
# 2. ฟังก์ชันดึงราคาน้ำมัน Realtime ยิงตรงบางจาก API
# ==========================================
@st.cache_data(ttl=1800) # ตั้งแคชไว้ครึ่งชั่วโมงเพื่อไม่ให้หน่วงหน้าแอปเวลาคลิกปุ่มอื่น
def fetch_bangchak_oil_prices():
    """
    ดึงราคาน้ำมัน Realtime ล่าสุดจาก API บางจากโดยตรง 
    """
    try:
        # ยิงตรงเข้า API ของปั๊มบางจากในไทย
        res = requests.get("https://oil-price.bangchak.co.th/apioilprice2/oilprice?lang=th", timeout=4)
        if res.status_code == 200:
            data = res.json()
            oil_data_list = data[0].get("Data", []) if isinstance(data, list) else data.get("data", [])
            
            realtime_oil = {}
            for item in oil_data_list:
                oil_name = item.get("OilName", "")
                oil_price = item.get("Price", item.get("PriceToday", 0))
                
                if not oil_price or float(oil_price) <= 0:
                    continue
                
                # แมปรายชื่อประเภทน้ำมันเข้าสู่ระบบ
                if "พรีเมียม ดีเซล" in oil_name or "Premium" in oil_name:
                    realtime_oil["ดีเซลหมุนเร็ว B7 (Premium)"] = float(oil_price)
                elif "ไฮดีเซล B7" in oil_name or "ดีเซล B7" in oil_name:
                    realtime_oil["ดีเซลหมุนเร็ว B7"] = float(oil_price)
                elif "แก๊สโซฮอล์ 95" in oil_name:
                    realtime_oil["แก๊สโซฮอล์ 95"] = float(oil_price)
                elif "แก๊สโซฮอล์ E20" in oil_name:
                    realtime_oil["แก๊สโซฮอล์ E20"] = float(oil_price)
                elif "แก๊สโซฮอล์ 91" in oil_name:
                    realtime_oil["แก๊สโซฮอล์ 91"] = float(oil_price)
                elif "แก๊สโซฮอล์ E85" in oil_name:
                    realtime_oil["แก๊สโซฮอล์ E85"] = float(oil_price)

            if "แก๊สโซฮอล์ 95" in realtime_oil:
                # เนื่องจากปั๊มบางจากไม่มีเบนซิน 95 เพียวขาย จึงคำนวณสัดส่วนเผื่อไว้ให้เมนูใช้งานได้ครบ
                realtime_oil["เบนซิน 95"] = round(realtime_oil["แก๊สโซฮอล์ 95"] + 7.50, 2)
                return realtime_oil, True
    except:
        pass
    return {}, False

# ประมวลผลดึงค่าเริ่มต้น Realtime 
oil_prices, api_success = fetch_bangchak_oil_prices()

# รายการประเภทน้ำมันทั้งหมดในระบบ
oil_options = [
    "ดีเซลหมุนเร็ว B7 (Premium)",
    "ดีเซลหมุนเร็ว B7",
    "แก๊สโซฮอล์ 95",
    "แก๊สโซฮอล์ E20",
    "แก๊สโซฮอล์ 91",
    "แก๊สโซฮอล์ E85",
    "เบนซิน 95"
]

# ==========================================
# 3. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ราคาน้ำมัน Realtime")
    
    # 1. เลือกประเภทน้ำมัน
    oil_type = st.selectbox("⛽ เลือกประเภทน้ำมัน", options=oil_options)
    
    # ดึงราคา Realtime ล่าสุดมาตั้งเป็นค่าดั้งเดิม ถ้าดึงไม่ได้จะปล่อยให้เป็น 0.0 เพื่อให้กรอกเอง
    fetched_price = oil_prices.get(oil_type, 0.0) if api_success else 0.0
    
    # 2. ช่องกรอกราคา (ดึงค่า Realtime มาใส่ให้อัตโนมัติ แต่ยังคงยอมให้พิมพ์แก้ไขเองได้ด้วย)
    THB_L = st.number_input(
        f"ราคาน้ำมันปัจจุบัน ({oil_type})", 
        min_value=0.0, 
        value=float(fetched_price), 
        step=0.1,
        help="ระบบจะพยายามดึงราคาสดจากบางจากให้ แต่คุณสามารถแก้ไขเลขเองได้ตลอดเวลา"
    )
    
    # พ่นตัวบอกสถานะให้ผู้ใช้ทราบ
    if api_success:
        st.success("✅ ดึงราคาน้ำมันสดจาก บางจาก API สำเร็จ")
    else:
        st.warning("⚠️ ดึง API ไม่สำเร็จชั่วคราว โปรดพิมพ์ระบุราคาน้ำมันด้วยตัวเองด้านบน")

    st.header("🚚 ข้อมูลตัวรถและพื้นที่บรรทุก")
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
    if THB_L <= 0:
        st.error("❌ ราคาน้ำมันต้องมากกว่า 0 บาท โปรดตรวจสอบราคาน้ำมันก่อนกดคำนวณ")
        st.stop()

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
