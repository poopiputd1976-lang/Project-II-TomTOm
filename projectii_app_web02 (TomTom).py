import streamlit as st
import math
import requests
from datetime import datetime
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from folium import plugins
from streamlit_folium import st_folium
import pandas as pd

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run Optimization", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางขนส่งนม (VRP Optimization)")
st.markdown("ระบบวิเคราะห์เส้นทางอัจฉริยะ พร้อมราคาน้ำมันกลาง อัปเดตล่าสุดปัจจุบัน")

# ==========================================
# 2. ฟังก์ชันดึงราคาน้ำมันปัจจุบัน (พร้อมแสดงวันที่อัปเดต)
# ==========================================
@st.cache_data(ttl=3600)  # ตั้ง Cache ให้ดึงใหม่ทุกๆ 1 ชั่วโมง เพื่อไม่ให้แอปช้าและป้องกันโดนบล็อก IP
def get_thailand_oil_prices():
    """
    ดึงราคาน้ำมันปัจจุบันจากฐานข้อมูลกลางที่มีบอทคอย Scraping อัปเดตให้ทุกเช้าอัตโนมัติ
    """
    # ราคาสำรองกรณีฉุกเฉินเชื่อมต่ออินเทอร์เน็ตไม่ได้
    default_oil = {
        "ดีเซลหมุนเร็ว B7 (Premium)": 44.94,
        "ดีเซลหมุนเร็ว B7": 32.94,
        "แก๊สโซฮอล์ 95": 38.55,
        "แก๊สโซฮอล์ E20": 36.44,
        "แก๊สโซฮอล์ 91": 38.18,
        "แก๊สโซฮอล์ E85": 36.14,
        "เบนซิน 95": 46.44
    }
    
    try:
        res = requests.get("https://raw.githubusercontent.com/piti118/thai-oil-price-api/main/oil_price.json", timeout=5)
        if res.status_code == 200:
            data = res.json()
            
            # ดึงวันที่และเวลาที่ราคาน้ำมันนี้ถูกอัปเดตล่าสุดปัจจุบัน
            last_update = data.get("update_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            
            # แมปชื่อตัวแปร JSON ให้กลายเป็นชื่อไทยที่เข้าใจง่ายในระบบ UI
            mapping = {
                "premium_diesel": "ดีเซลหมุนเร็ว B7 (Premium)",
                "diesel_b7": "ดีเซลหมุนเร็ว B7",
                "gasohol_95": "แก๊สโซฮอล์ 95",
                "gasohol_e20": "แก๊สโซฮอล์ E20",
                "gasohol_91": "แก๊สโซฮอล์ 91",
                "gasohol_e85": "แก๊สโซฮอล์ E85",
                "benzine_95": "เบนซิน 95"
            }
            
            realtime_oil = {}
            prices_today = data.get("prices", {})
            for k, v in mapping.items():
                if k in prices_today:
                    realtime_oil[v] = float(prices_today[k])
            
            if realtime_oil:
                return realtime_oil, last_update, True
    except:
        pass
    
    return default_oil, "ใช้ราคาสำรองในระบบ", False

# เรียกใช้งานฟังก์ชันดึงราคาน้ำมันปัจจุบัน
oil_prices, oil_last_update, api_success = get_thailand_oil_prices()

# ==========================================
# 3. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("🚚 การจัดส่งและยานพาหนะ")
    NUM_VEHICLES = st.number_input("จำนวนรถที่มีในระบบ (คัน)", min_value=1, value=2, step=1)
    NUM_COOLERS = st.number_input("จำนวนถังแช่ต่อรถ 1 คัน (ใบ)", min_value=1, value=2)
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=75.0)
    DEAD_SPACE_RATIO = 0.15 

    st.header("⛽ ต้นทุนและราคาน้ำมันกลาง")
    oil_type = st.selectbox("⛽ เลือกประเภทน้ำมัน", options=list(oil_prices.keys()))
    
    # ดึงราคาที่อัปเดตล่าสุดปัจจุบันมาใส่เป็นค่าเริ่มต้น
    suggested_price = oil_prices[oil_type]
    THB_L = st.number_input(f"ราคาขายปลีกกลาง ({oil_type})", min_value=1.0, value=float(suggested_price), step=0.1)
    
    # แสดงสถานะการดึงราคาน้ำมันกลางปัจจุบันแก่ผู้ใช้
    if api_success:
        st.success(f"✅ เชื่อมต่อราคาน้ำมันกลางปัจจุบันสำเร็จ\n\n(อัปเดตล่าสุด: {oil_last_update})")
    else:
        st.warning(f"⚠️ {oil_last_update}")

    KM_L = st.number_input("อัตราสิ้นเปลือง (km/L)", min_value=1.0, value=10.0, step=0.5)

TOTAL_NET_CAPACITY = int((450 - ICE_PER_COOLER) * NUM_COOLERS)

# ==========================================
# 4. จัดการข้อมูลนำเข้า
# ==========================================
st.subheader("📍 นำเข้าข้อมูลจุดจัดส่ง")
st.caption("🚨 ข้อควรระวัง: ข้อมูลแถวแรกสุด (Index 0) ในไฟล์ จะถูกตั้งให้เป็น 'จุดฟาร์มต้นทาง/คลังสินค้า' เสมอ")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel หรือ CSV)", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        # ดึงข้อมูลไฟล์ พร้อมเติมค่าว่าง (NaN) เป็น 0 เผื่อผู้ใช้กรอกข้อมูลไม่ครบ สูตรจะได้ไม่พัง
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        df = df.fillna(0)
        edited_df = st.data_editor(df, num_rows="dynamic", height=250, use_container_width=True)
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์ข้อมูลลูกค้าเพื่อเริ่มการวิเคราะห์")
    st.stop()

# --- ฟังก์ชันช่วยคำนวณระยะทางทางตรงเชิงพิกัดโลก ---
def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))))

# ==========================================
# 5. ประมวลผลสร้างเส้นทางวิ่ง (Optimization)
# ==========================================
if st.button("🚀 ประมวลผลเส้นทาง", type="primary", use_container_width=True):
    if edited_df.empty or len(edited_df) < 2:
        st.error("❌ กรุณาเพิ่มข้อมูลพิกัดในตารางอย่างน้อย 2 แถวขึ้นไป (คลังสินค้า 1 จุด + ลูกค้าอย่างน้อย 1 จุด)")
        st.stop()
        
    demands = []
    # รีเซ็ตอินเดกซ์แถวเพื่อให้เริ่มต้นที่ 0 เสมอ ป้องกันการบั้มเวลาผู้ใช้กดลบแถวบนหน้าเว็บ
    edited_df = edited_df.reset_index(drop=True)
    
    for i, row in edited_df.iterrows():
        if i == 0: 
            demands.append(0) 
            continue
        
        # ค้นหาคอลัมน์ไม่ว่าจะพิมพ์เล็กหรือใหญ่ ป้องกันโปรแกรมหาหัวข้อคอลัมน์ไม่เจอ
        vol_200cc = float(row.get("200cc", row.get("200CC", 0))) * 0.2
        vol_2l = float(row.get("2L", row.get("2l", 0))) * 2.0
        vol_5l = float(row.get("5L", row.get("5l", 0))) * 5.0
        
        vol = vol_200cc + vol_2l + vol_5l
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    with st.spinner('กำลังวิเคราะห์โครงสร้างเส้นทางที่ดีที่สุด...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        # ปรับแก้ตัวแปรให้โปรแกรมรองรับการสลับวิ่งด้วยรถจำนวน NUM_VEHICLES คัน
        manager = pywrapcp.RoutingIndexManager(len(coords), int(NUM_VEHICLES), 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_idx, to_idx):
            from_node = manager.IndexToNode(from_idx)
            to_node = manager.IndexToNode(to_idx)
            d = dist_matrix[from_node][to_node]
            # ตั้งความเร็วเฉลี่ยรถขนส่งนมที่ 40 กม./ชม. เพื่อความใกล้เคียงถนนประเทศไทย
            return int((d/1000)/40*60) + (math.ceil(SERVICE_TIME_SEC/60) if from_node != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        # ควบคุมความจุไม่ให้นมล้นรถ
        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, [TOTAL_NET_CAPACITY] * int(NUM_VEHICLES), True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        solution = routing.SolveWithParameters(search_params)

    if solution:
        st.success("🎯 ประมวลผลเส้นทางอัจฉริยะเสร็จสิ้น!")
        
        # สร้างแผนที่และเซ็ตสีแบ่งตามรถแต่ละคัน
        m = folium.Map(location=coords[0], zoom_start=11)
        colors = ["#2980B9", "#27AE60", "#8E44AD", "#D35400", "#C0392B", "#16A085"]
        
        total_all_dist = 0
        total_all_cost = 0
        
        # ค้นหาและวาดเส้นทางตามคิวรถทีละคัน
        for vehicle_id in range(int(NUM_VEHICLES)):
            route_indices = []
            index = routing.Start(vehicle_id)
            
            while not routing.IsEnd(index):
                route_indices.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            route_indices.append(0)
            
            # หากรถคันดังกล่าวไม่ได้ถูกเรียกใช้งาน ให้ข้ามการวาดเส้นทางไป
            if len(route_indices) <= 2:
                continue
                
            # ยิงดึงพิกัดถนนจริงผ่าน TomTom API ของรถคันนั้นๆ
            url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join([f'{coords[n][0]},{coords[n][1]}' for n in route_indices])}/json"
            res = requests.get(url, params={"key": API_KEY, "travelMode": "truck"})
            
            if res.status_code == 200:
                route_data = res.json()['routes'][0]
                summary = route_data['summary']
                dist_km = summary['lengthInMeters'] / 1000
                cost = (dist_km / KM_L) * THB_L
                
                total_all_dist += dist_km
                total_all_cost += cost
                
                # วาดเส้นทางการเดินรถแบบมี Animation แอนิเมชันลูกศรวิ่งตามทิศทาง
                all_points = [[p['latitude'], p['longitude']] for leg in route_data['legs'] for p in leg['points']]
                color_choice = colors[vehicle_id % len(colors)]
                plugins.AntPath(locations=all_points, color=color_choice, weight=5, delay=1200).add_to(m)
                
                # ทำเครื่องหมายปักหมุดจุดส่งสินค้าลงบนแผนที่
                for idx_seq, n in enumerate(route_indices[:-1]):
                    if n == 0:
                        folium.Marker(coords[n], popup="ฟาร์มต้นทาง (Depot)", icon=folium.Icon(color='red', icon='home')).add_to(m)
                    else:
                        folium.Marker(coords[n], popup=f"รถคันที่ {vehicle_id+1} | ส่งคิวที่ {idx_seq}", icon=folium.Icon(color='blue')).add_to(m)
            else:
                st.warning(f"⚠️ TomTom API ขัดข้องหรือไม่สามารถดึงข้อมูลของ รถคันที่ {vehicle_id+1} ได้")

        # สรุปผลลัพธ์ผ่าน Dashboard ด้านบนแผนที่
        c1, c2, c3 = st.columns(3)
        c1.metric("ระยะทางวิ่งรวมทั้งหมด", f"{total_all_dist:.2f} กม.")
        c2.metric("คิดเป็นเงินต้นทุนน้ำมันรวม", f"฿{total_all_cost:.2f}")
        c3.metric("ปริมาณน้ำมันที่ใช้ไป", f"{total_all_dist / KM_L:.2f} ลิตร")
        
        # เรนเดอร์แผนที่ Folium ขึ้นหน้าจอ Streamlit
        st_folium(m, width="100%", height=500)
        
    else:
        st.error("❌ ไม่สามารถคำนวณจัดสรรเส้นทางได้! โปรดตรวจสอบว่าจำนวนรถมีเพียงพอหรือไม่ หรือสินค้าในบางจุดมีปริมาณเกินความจุที่รถคันเดียวจะรับไหวหรือไม่")
