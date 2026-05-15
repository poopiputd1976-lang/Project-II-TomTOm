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

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run Optimization", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางขนส่งนม (VRP Optimization)")
st.markdown("ระบบวิเคราะห์เส้นทางอัจฉริยะ พร้อมการนำทางจริงและฟังก์ชันดึงราคาน้ำมันสดจากเว็บ")


# ==========================================
# 2. ฟังก์ชันดึงราคาน้ำมันสด (Real-time Thailand Oil Price API)
# ==========================================
@st.cache_data(ttl=3600)
def get_thailand_oil_prices():
    # ราคาส่วนนี้จะใช้เป็นตัวสำรอง (Fallback) กรณีเว็บล่ม
    fallback_oil = {
        "ดีเซลหมุนเร็ว B7": 32.94,
        "แก๊สโซฮอล์ 95": 37.75,
        "แก๊สโซฮอล์ E20": 35.64,
        "แก๊สโซฮอล์ 91": 37.38,
        "เบนซิน 95": 45.64
    }
    
    try:
        # ใช้ API สาธารณะที่ดึงราคาน้ำมันจาก ปตท. (เสถียรกว่าเดิม)
        url = "https://api.sumitpost.com/api/oil-prices"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            raw_data = response.json()
            # เข้าไปดึงข้อมูลในส่วนของ PTT (OR)
            ptt_prices = raw_data.get("data", {}).get("stations", {}).get("PTT", {})
            
            if ptt_prices:
                live_data = {}
                for name, info in ptt_prices.items():
                    live_data[name] = float(info['price'])
                return live_data, True
                
        return fallback_oil, False
    except:
        return fallback_oil, False

# เรียกใช้งานฟังก์ชัน
oil_prices, is_live = get_thailand_oil_prices()


# ==========================================
# 3. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ต้นทุนและพื้นที่บรรทุก")
    
    # เลือกประเภทน้ำมันจากข้อมูลที่ดึงมาได้
    oil_type = st.selectbox(
        "⛽ เลือกประเภทน้ำมันของรถขนส่ง", 
        options=list(oil_prices.keys()),
        index=0
    )
    
    # ดึงราคากลางมาตั้งต้น
    suggested_price = oil_prices[oil_type]
    
    # ช่องแสดงราคา (ผู้ใช้ยังคงพิมพ์แก้เองได้ถ้าต้องการ)
    THB_L = st.number_input(
        f"ราคาน้ำมัน ({oil_type}) (THB/L)", 
        min_value=1.0, 
        value=float(suggested_price), 
        step=0.01, 
        format="%.2f"
    )
    
    # แสดงสถานะการดึงข้อมูล
    if is_live:
        st.success("🌐 ดึงราคากลางล่าสุดจาก ปตท. สำเร็จ")
    else:
        st.warning("⚠️ ดึงราคาจากเว็บไม่สำเร็จ กำลังใช้ราคาสันนิษฐาน (สำรอง)")

    KM_L = st.number_input("อัตราสิ้นเปลือง (km/L)", min_value=1.0, value=10.0, step=0.5, format="%.2f")
    NUM_COOLERS = st.number_input("จำนวนถัง (ใบ)", min_value=1, value=2, step=1)
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=75.0, step=1.0)
    DEAD_SPACE_RATIO = 0.15 
    
    st.header("🚧 ข้อจำกัดเส้นทาง")
    TRAVEL_MODE = st.selectbox("ประเภทยานพาหนะ", ["car", "van", "motorcycle", "truck"], index=1) 
    
    AVOID_AREA = st.text_area("พิกัดพื้นที่ห้ามผ่าน (Lat,Lon:Lat,Lon)", value="", height=100)

# คำนวณความจุรถ
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
        edited_df = st.data_editor(df, num_rows="dynamic", height=250, use_container_width=True)
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์ข้อมูลลูกค้าเพื่อเริ่มการวิเคราะห์")
    st.stop()

# --- ส่วนคำนวณเบื้องต้น ---
def time_to_min(t_str):
    try:
        h, m = map(int, str(t_str).split(':'))
        return h * 60 + m
    except: return None 

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

# ==========================================
# 5. ประมวลผล (Optimization)
# ==========================================
st.markdown("---")
if st.button("🚀 ประมวลผลเส้นทางและวิเคราะห์เปรียบเทียบ", type="primary", use_container_width=True):
    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (float(row.get("200cc", 0)) * 0.2) + (float(row.get("2L", 0)) * 2.0) + (float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    if sum(demands) > TOTAL_NET_CAPACITY:
        st.error(f"❌ น้ำหนักรวมเกินความจุรถ ({TOTAL_NET_CAPACITY} L)")
        st.stop()
        
    with st.spinner('กำลังใช้สมองกลคำนวณเส้นทาง...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        baseline_km = sum([dist_matrix[i][i+1] for i in range(len(coords)-1)] + [dist_matrix[len(coords)-1][0]]) / 1000
        
        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        routing.AddDimension(transit_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.CumulVar(routing.Start(0)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            time_dim.CumulVar(idx).SetRange(s, 2880)
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100)

        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, [TOTAL_NET_CAPACITY], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
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
        api_params = {"key": API_KEY, "travelMode": TRAVEL_MODE}
        
        # จัดการ AVOID_AREA
        rectangles = []
        if AVOID_AREA.strip() != "":
            for line in AVOID_AREA.strip().split('\n'):
                line = line.strip()
                if not line: continue
                try:
                    p1, p2 = line.split(':')
                    lat1, lon1 = map(float, p1.split(','))
                    lat2, lon2 = map(float, p2.split(','))
                    rectangles.append({
                        "southWestCorner": {"latitude": min(lat1, lat2), "longitude": min(lon1, lon2)},
                        "northEastCorner": {"latitude": max(lat1, lat2), "longitude": max(lon1, lon2)}
                    })
                except: pass
            
        if rectangles:
            res = requests.post(url, params=api_params, json={"avoidAreas": {"rectangles": rectangles}})
        else:
            res = requests.get(url, params=api_params)
        
        if res.status_code == 200:
            route_data = res.json()['routes'][0]
            summary = route_data['summary']
            dist_km = summary['lengthInMeters'] / 1000
            cost = (dist_km / KM_L) * THB_L
            
            # Dashboard
            st.subheader("📊 การวิเคราะห์ผลลัพธ์รวม")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("ระยะทางจริง", f"{dist_km:.2f} กม.")
            c2.metric("ต้นทุนน้ำมัน", f"฿{cost:.2f}")
            c3.metric("CO2 ทั้งเที่ยว", f"{(dist_km/KM_L)*EMISSION_FACTOR:.2f} kg")
            hh, mm = divmod(summary['travelTimeInSeconds'] // 60, 60)
            c4.metric("เวลาเดินทางรวม", f"{int(hh)} ชม. {int(mm)} นาที")

            # แสดงแผนที่ Folium
            st.subheader("🗺️ แผนที่เส้นทาง")
            m = folium.Map(location=coords[0], zoom_start=14)
            all_points = [[p['latitude'], p['longitude']] for leg in route_data['legs'] for p in leg['points']]
            plugins.AntPath(locations=all_points, color="#2980B9").add_to(m)
            
            for i, n in enumerate(route_indices[:-1]):
                loc = edited_df.iloc[n]
                if n == 0:
                    folium.Marker([loc['Lat'], loc['Lon']], popup="ฟาร์ม", icon=folium.Icon(color='green', icon='home')).add_to(m)
                else:
                    icon_html = f'''<div style="font-size: 11pt; color: white; background-color: #E74C3C; border-radius: 50%; text-align: center; width: 28px; height: 28px; line-height: 28px;">{i}</div>'''
                    folium.Marker([loc['Lat'], loc['Lon']], icon=folium.DivIcon(html=icon_html)).add_to(m)

            st_folium(m, width="100%", height=500)

            # ตารางงาน
            st.subheader("📋 ตารางวิเคราะห์คิวงาน")
            schedule = []
            curr_time = datetime.combine(datetime.today(), DEPART_TIME)
            for i, n in enumerate(route_indices[:-1]):
                t_min, l_dist = 0, 0.0
                if i > 0:
                    leg = route_data['legs'][i-1]['summary']
                    t_min = math.ceil(leg['travelTimeInSeconds'] / 60)
                    l_dist = leg['lengthInMeters'] / 1000
                    curr_time += timedelta(minutes=t_min)
                
                schedule.append({
                    "คิว": i, "สถานที่": edited_df.iloc[n]["ชื่อสถานที่"], 
                    "เวลาถึง": curr_time.strftime("%H:%M"), "ระยะทาง (กม.)": f"{l_dist:.2f}"
                })
                curr_time += timedelta(seconds=SERVICE_TIME_SEC)
            st.dataframe(pd.DataFrame(schedule), use_container_width=True)
    else:
        st.error("❌ หาเส้นทางไม่ได้")
