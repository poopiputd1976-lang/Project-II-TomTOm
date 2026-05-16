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
# 0. ฟังก์ชันคำนวณและความปลอดภัยพื้นฐาน
# ==========================================
def safe_float(val):
    try:
        if pd.isna(val): return 0.0
        return float(val)
    except:
        return 0.0

@st.cache_data(ttl=21600) 
def fetch_today_oil_price():
    try:
        url = "https://api.chnwt.dev/thai-oil-api/latest"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            ptt_prices = data['response']['stations']['ptt']
            date_str = data['response']['date']
            
            target_types = ["ดีсел", "แก๊สโซฮอล์ 91", "แก๊สโซฮอล์ 95"]
            oil_options = {}
            
            for key, val in ptt_prices.items():
                name = val['name']
                if any(target in name for target in target_types):
                    if "พรีเมียม" not in name and val['price'] and val['price'] != "-":
                        oil_options[name] = float(val['price'])
            return oil_options, date_str
    except Exception:
        pass
    return None, None

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

def time_to_min(t_str):
    try:
        h, m = map(int, str(t_str).split(':'))
        return h * 60 + m
    except: return None 

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Ultimate Milk Run Optimization v2", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางจัดส่งอัจฉริยะ (Operational v2 - Enterprise Edition)")
st.markdown("ระบบคำนวณกองรถ VRP ควบคุมความจุและเวลา พร้อมระบบป้องกันโมเดลพัง (Disjunction Penalty) และคำนวณเวลาตามสภาพจราจรจริง")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน & บัฟเฟอร์เวลา")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    
    # 🌟 [อัปเกรดที่ 1] ปรับเวลาจอดฐาน + เวลาแปรผันตามปริมาณนม (Dynamic Service Time)
    st.markdown("**⚙️ ตั้งเวลาบริการหน้างาน (Service Time)**")
    BASE_SERVICE_MIN = st.number_input("เวลาจอดตรวจเช็คฐาน (นาที/จุด)", min_value=0, value=2, step=1)
    PER_LITER_SEC = st.number_input("เวลาขนย้ายนมเพิ่มเติม (วินาที/ลิตร)", min_value=0.0, value=3.0, step=0.5)
    
    # 🌟 [อัปเกรดที่ 2] เพิ่มตัวคูณสภาพจราจร (Traffic Padding Factor) ป้องกันเวลาโลกสวยเกินจริง
    st.markdown("**🚗 บัฟเฟอร์เผื่อรถติด (Traffic Factor)**")
    TRAFFIC_MULTIPLIER = st.slider(
        "ตัวคูณเวลาเดินทางตามสภาพจราจร", 
        min_value=1.0, max_value=2.5, value=1.3, step=0.1,
        help="1.0 = วิ่งความเร็วปกติ, 1.3 = เผื่อรถติดเขตเมือง/ทำถนน 30%, 1.8+ = รถติดหนักช่วงเร่งด่วน"
    )
    
    st.header("⛽ ราคาน้ำมัน Real-time")
    oil_data, update_date = fetch_today_oil_price()
    if oil_data:
        st.success(f"อัปเดตราคาล่าสุด: {update_date}")
        selected_oil = st.selectbox("เลือกชนิดน้ำมัน", list(oil_data.keys()))
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", value=float(oil_data[selected_oil]), step=0.5, format="%.2f")
    else:
        st.warning("⚠️ ไม่สามารถดึงข้อมูลราคา Real-time ได้ (ใช้ราคาประเมิน)")
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=35.0, step=0.5, format="%.2f")

    st.header("🚛 ตั้งค่านโยบายและกองรถ")
    ROUTE_TYPE = st.selectbox(
        "🛣️ รูปแบบการเลือกเส้นทางของระบบ", 
        ["fastest", "shortest"], 
        index=0, 
        format_func=lambda x: "⚡ Quickest (เน้นทางที่เร็วที่สุด เลี่ยงรถติด)" if x == "fastest" else "📏 Shortest (เน้นทางที่สั้นที่สุด เซฟระยะทางไมล์รถ)"
    )
    
    FLEET_MODE = st.radio(
        "🎯 โหมดการทำงานของกองรถ (Fleet Mode)",
        ["🟢 เน้นประหยัดต้นทุนที่สุด (Cost Saving)", "🔵 บังคับเฉลี่ยงานให้รถทุกคัน (Balanced Workload)"]
    )
    
    st.markdown("---")
    st.subheader("📦 ข้อมูลสเปครถยนต์ในกอง")
    
    if 'num_vehicles' not in st.session_state:
        st.session_state.num_vehicles = 2
        
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("➕ เพิ่มรถ 1 คัน", use_container_width=True):
            st.session_state.num_vehicles += 1
    with col_btn2:
        if st.button("➖ ลดรถ 1 คัน", use_container_width=True) and st.session_state.num_vehicles > 1:
            st.session_state.num_vehicles -= 1
            
    st.caption(f"ปัจจุบันมีรถสแตนด์บายทั้งหมด: **{st.session_state.num_vehicles}** คัน")
    
    vehicles_data = []
    for v_idx in range(st.session_state.num_vehicles):
        with st.expander(f"🚚 รถคันที่ {v_idx + 1}", expanded=(v_idx == 0)):
            v_mode = st.selectbox(f"ประเภทรถ คันที่ {v_idx+1}", ["truck", "van", "car", "motorcycle"], key=f"mode_{v_idx}")
            v_kml = st.number_input(f"อัตราสิ้นเปลือง (km/L) คันที่ {v_idx+1}", min_value=1.0, value=10.0, step=0.5, key=f"kml_{v_idx}")
            v_coolers = st.number_input(f"จำนวนถัง (ใบ) คันที่ {v_idx+1}", min_value=1, value=2, step=1, key=f"coolers_{v_idx}")
            v_ice = st.number_input(f"น้ำแข็ง/ถัง (L) คันที่ {v_idx+1}", min_value=0.0, value=75.0, step=1.0, key=f"ice_{v_idx}")
            
            v_capacity = int((450 - v_ice) * v_coolers)
            st.info(f"ความจุสุทธิ: {v_capacity} L")
            
            vehicles_data.append({
                "id": v_idx,
                "mode": v_mode,
                "km_l": v_kml,
                "capacity": v_capacity
            })
            
    DEAD_SPACE_RATIO = 0.15 
    EMISSION_FACTOR = 2.70757206 
    
    st.markdown("---")
    st.header("🚧 พื้นที่ห้ามผ่าน")
    AVOID_AREA = st.text_area("พิกัดพื้นที่ห้ามผ่าน (Lat,Long มุมที่ 1 : Lat,Long มุมที่ 2)", value="", height=80)

ROUTE_COLORS = ["#2980B9", "#27AE60", "#E67E22", "#8E44AD", "#16A085", "#C0392B", "#F39C12"]

# ==========================================
# 3. จัดการข้อมูลสัญญาส่งมอบนม
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

# ==========================================
# 4. ประมวลผลคณิตศาสตร์ (Ultimate VRP Core Logic)
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณโมเดลจำลองเส้นทางขั้นสูง", type="primary", use_container_width=True):
    # คำนวณ Demand รายจุด
    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: 
            demands.append(0)
            continue
        vol = (safe_float(row.get("200cc", 0)) * 0.2) + (safe_float(row.get("2L", 0)) * 2.0) + (safe_float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    total_fleet_capacity = sum([v['capacity'] for v in vehicles_data])
    if sum(demands) > total_fleet_capacity:
        st.error(f"❌ ปริมาณนมรวม ({sum(demands)} L) เกินความจุรวมของกองรถทั้งหมดที่มี ({total_fleet_capacity} L)")
        st.stop()
        
    with st.spinner('กำลังประมวลผลอัลกอริทึม Hybrid VRP ควบคู่กับวิเคราะห์พิกัด TomTom...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        num_vehicles = len(vehicles_data)
        manager = pywrapcp.RoutingIndexManager(len(coords), num_vehicles, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        # 🌟 ลอจิกมิติเวลาอัปเกรด (ความเร็วรถ + บัฟเฟอร์รถติด + เวลาจอดแปรผันตามจำนวนนมจริง)
        def time_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            
            d = dist_matrix[from_node][to_node]
            speed_kmh = 30 if ROUTE_TYPE == "fastest" else 25 
            
            # เวลาเดินทางฐาน (นาที) + คูณบัฟเฟอร์จราจรตัวจริงหนีความโลกสวย
            travel_time_min = ((d / 1000) / speed_kmh * 60) * TRAFFIC_MULTIPLIER
            
            # เวลาบริการหน้างานแปรผันตามขนาดของนมที่จุดต้นทาง (ถ้าไม่ใช่จุดเริ่ม)
            service_time_min = 0
            if from_node != 0:
                node_milk_volume = demands[from_node]
                service_time_min = BASE_SERVICE_MIN + ((node_milk_volume * PER_LITER_SEC) / 60)
                
            return int(travel_time_min + math.ceil(service_time_min))
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        # เพิ่มมิติเวลา (Time Dimension) เข้า Routing Model
        routing.AddDimension(transit_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        
        if "🔵 บังคับเฉลี่ยงาน" in FLEET_MODE:
            time_dim.SetGlobalSpanCostCoefficient(120) 
        
        for v_idx in range(num_vehicles):
            time_dim.CumulVar(routing.Start(v_idx)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        # เงื่อนไขเวลาของลูกค้า (Time Windows)
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            time_dim.CumulVar(idx).SetRange(s, 2880)
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100)

        # 🌟 [อัปเกรดที่ 3] เปิดมิติระบบ Disjunction Penalty ป้องกันโค้ดค้าง/พัง เมื่อกรอกเงื่อนไขแน่นเกินไป
        penalty_value = 100000
        for node in range(1, len(coords)):
            routing.AddDisjunction([manager.NodeToIndex(node)], penalty_value)

        # มิติความจุถังนม (Capacity Dimension)
        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        fleet_capacities = [v['capacity'] for v in vehicles_data]
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, fleet_capacities, True, "Capacity")

        # ค้นหาคำตอบด้วย Guided Local Search
        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
        solution = routing.SolveWithParameters(search_params)

    if solution:
        fleet_results = {}
        total_dist_km, total_cost, total_co2, total_time_sec = 0.0, 0.0, 0.0, 0
        
        rectangles = []
        if AVOID_AREA.strip() != "":
            for line in AVOID_AREA.strip().split('\n'):
                line = line.strip()
                if not line: continue
                try:
                    p1_str, p2_str = line.split(':')
                    lat1, lon1 = map(float, p1_str.split(','))
                    lat2, lon2 = map(float, p2_str.split(','))
                    rectangles.append({
                        "southWestCorner": {"latitude": min(lat1, lat2), "longitude": min(lon1, lon2)},
                        "northEastCorner": {"latitude": max(lat1, lat2), "longitude": max(lon1, lon2)}
                    })
                except: pass

        # ค้นหาจุดที่โดนดรอปออกเนื่องจากเวลาหน้างานวิ่งไม่ทันแน่ ๆ
        dropped_nodes = []
        for node in range(len(coords)):
            if routing.IsStart(manager.NodeToIndex(node)) or routing.IsEnd(manager.NodeToIndex(node)):
                continue
            if solution.Value(routing.NextVar(manager.NodeToIndex(node))) == manager.NodeToIndex(node):
                dropped_nodes.append(node)

        # จัดการข้อมูลรายคันรถ
        for v_idx in range(num_vehicles):
            route_indices = []
            index = routing.Start(v_idx)
            while not routing.IsEnd(index):
                route_indices.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            route_indices.append(0)
            
            if len(route_indices) <= 2:
                continue
                
            v_info = vehicles_data[v_idx]
            url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join([f'{coords[n][0]},{coords[n][1]}' for n in route_indices])}/json"
            
            api_params = {
                "key": API_KEY, 
                "travelMode": v_info['mode'],
                "routeType": ROUTE_TYPE  
            }
            
            if rectangles:
                res = requests.post(url, params=api_params, json={"avoidAreas": {"rectangles": rectangles}})
            else:
                res = requests.get(url, params=api_params)
                
            if res.status_code == 200:
                route_data = res.json()['routes'][0]
                summary = route_data['summary']
                
                v_dist = summary['lengthInMeters'] / 1000
                v_cost = (v_dist / v_info['km_l']) * THB_L
                v_co2 = (v_dist / v_info['km_l']) * EMISSION_FACTOR
                
                total_dist_km += v_dist
                total_cost += v_cost
                total_co2 += v_co2
                total_time_sec += summary['travelTimeInSeconds']
                
                fleet_results[v_idx] = {
                    "indices": route_indices,
                    "route_data": route_data,
                    "dist_km": v_dist,
                    "cost": v_cost,
                    "co2": v_co2,
                    "time_sec": summary['travelTimeInSeconds'],
                    "config": v_info
                }
            else:
                st.error(f"❌ TomTom API ปฏิเสธการทำงานของรถคันที่ {v_idx+1}: {res.text}")
                st.stop()

        # แจ้งเตือนเรื่องจุดจัดส่งที่ระบบจำเป็นต้องคัดออกกรณีทำเวลาไม่ทันจริง ๆ
        if dropped_nodes:
            st.warning(f"⚠️ มีจุดส่งของจำนวน {len(dropped_nodes)} จุดที่เงื่อนไขเวลาแน่นเกินไป ระบบจึงคัดออกเพื่อความปลอดภัยหน้างาน")
            with st.expander("🔍 ดูรายชื่อสถานที่ที่ข้ามไปในวันนี้"):
                for dn in dropped_nodes:
                    st.write(f"- ❌ [{edited_df.iloc[dn].get('ชื่อสถานที่', 'ไม่มีชื่อ')}] (ต้องส่งก่อน: {edited_df.iloc[dn].get('ต้องส่งก่อน', '-')})")

        # --- KPI Dashboard ---
        st.subheader("📊 บทวิเคราะห์ผลลัพธ์กองรถและเส้นทาง (KPI Dashboard)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ระยะทางวิ่งรวมกองรถ", f"{total_dist_km:.2f} กม.")
        c2.metric("งบประมาณค่าน้ำมันรวมทั้งหมด", f"฿{total_cost:.2f}")
        c3.metric("ปริมาณการปล่อยก๊าซ CO₂ รวม", f"{total_co2:.2f} kg-CO₂")
        hh, mm = divmod(total_time_sec // 60, 60)
        c4.metric("เวลารวมในภารกิจ", f"{int(hh)} ชม. {int(mm)} นาที" if hh > 0 else f"{int(mm)} นาที")

        # --- แผนที่และตารางคิวงาน ---
        col_map, col_table = st.columns([1.3, 1.7])
        with col_map:
            st.subheader("🗺️ แผนที่เส้นทางแยกสีคันรถ")
            m = folium.Map(location=coords[0], zoom_start=12, control_scale=True)
            FloatImage("https://upload.wikimedia.org/wikipedia/commons/e/ec/Compass_rose_n_blank.svg", bottom=5, left=90, width="6%").add_to(m)
            
            folium.TileLayer(
                tiles=f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/{{z}}/{{x}}/{{y}}.png?key={API_KEY}",
                attr='TomTom Traffic', name='ปริมาณสภาพจราจร (Traffic)', overlay=True, control=True, opacity=0.5
            ).add_to(m)

            for color_i, (v_idx, res_data) in enumerate(fleet_results.items()):
                all_points = []
                for leg in res_data['route_data']['legs']:
                    for p in leg['points']: all_points.append([p['latitude'], p['longitude']])
                
                color = ROUTE_COLORS[color_i % len(ROUTE_COLORS)]
                plugins.AntPath(
                    locations=all_points, delay=1000, dash_array=[15, 30],
                    color=color, pulse_color="#FFFFFF", weight=5, opacity=0.8,
                    name=f'รถคันที่ {v_idx+1} ({res_data["config"]["mode"]})'
                ).add_to(m)

                for q_i, n in enumerate(res_data['indices'][:-1]):
                    loc = edited_df.iloc[n]
                    if n == 0 and color_i == 0:
                        folium.Marker([loc['Lat'], loc['Lon']], popup="ฟาร์ม (จุดเริ่มต้น)", icon=folium.Icon(color='green', icon='home')).add_to(m)
                    elif n > 0:
                        icon_html = f'''<div style="font-size: 10pt; font-weight: bold; color: white; background-color: {color}; border: 2px solid white; border-radius: 50%; text-align: center; width: 24px; height: 24px; line-height: 20px;">{q_i}</div>'''
                        folium.Marker([loc['Lat'], loc['Lon']], popup=f"รถคันที่ {v_idx+1} คิวที่ {q_i}: {loc['ชื่อสถานที่']}", icon=folium.DivIcon(html=icon_html)).add_to(m)

            folium.LayerControl().add_to(m)
            st_folium(m, width="100%", height=520, returned_objects=[])

        with col_table:
            st.subheader("📋 แผนการเดินรถรายวันแยกรายคันรถ")
            tabs = st.tabs([f"🚚 คันที่ {v_idx+1} ({res_data['config']['mode']})" for v_idx, res_data in fleet_results.items()])
            
            for tab_i, (v_idx, res_data) in enumerate(fleet_results.items()):
                with tabs[tab_i]:
                    st.markdown(f"**ระยะทางเที่ยวนี้:** {res_data['dist_km']:.2f} กม. | **ค่าน้ำมัน:** ฿{res_data['cost']:.2f} | **ความจุสูงสุดรถ:** {res_data['config']['capacity']} L")
                    
                    schedule = []
                    curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                    r_indices = res_data['indices']
                    v_loaded_milk = 0 
                    
                    # 🌟 [อัปเกรดที่ 4] ตัวแปรสร้างข้อความสรุปใบงานส่งเข้ากลุ่ม LINE รายวัน
                    line_text_summary = f"🚚 **ใบงานจัดส่ง: รถคันที่ {v_idx+1} ({res_data['config']['mode'].upper()})**\n"
                    line_text_summary += f"• ระยะทาง: {res_data['dist_km']:.2f} กม. | ค่าน้ำมันประเมิน: ฿{res_data['cost']:.2f}\n"
                    line_text_summary += f"• เวลาออกรถจากฟาร์ม: {DEPART_TIME.strftime('%H:%M')} น.\n\n📍 *ลำดับคิวงานคนขับรถ:*\n"
                    
                    for i in range(len(r_indices)):
                        n = r_indices[i]
                        t_min, l_dist = 0, 0.0
                        loc_data = edited_df.iloc[n]
                        
                        if i > 0:
                            leg = res_data['route_data']['legs'][i-1]['summary']
                            t_min = math.ceil((leg['travelTimeInSeconds'] / 60) * TRAFFIC_MULTIPLIER)
                            l_dist = leg['lengthInMeters'] / 1000
                            curr_time += timedelta(minutes=t_min)
                        
                        maps_url = f"https://www.google.com/maps/search/?api=1&query={loc_data['Lat']},{loc_data['Lon']}"
                        
                        if i == 0: 
                            display_name = f"{loc_data['ชื่อสถานที่']} (จุดสตาร์ท)"
                            line_text_summary += f"  {i}. [{curr_time.strftime('%H:%M')}] 🏠 ฟาร์มต้นทาง\n"
                        elif i == len(r_indices) - 1: 
                            display_name = f"{loc_data['ชื่อสถานที่']} (กลับเข้าฟาร์ม)"
                            line_text_summary += f"  🏁 [{curr_time.strftime('%H:%M')}] กลับเข้าฟาร์ม (จบงาน)\n"
                        else: 
                            display_name = loc_data["ชื่อสถานที่"]
                            line_text_summary += f"  {i}. [{curr_time.strftime('%H:%M')}] {display_name} (ส่งนม {demands[n]} L)\n     🔗 แผนที่: {maps_url}\n"
                        
                        node_demand = demands[n]
                        v_loaded_milk += node_demand

                        schedule.append({
                            "คิว": i if i < len(r_indices)-1 else "🏁", 
                            "สถานที่": display_name, 
                            "เวลาที่ถึง": curr_time.strftime("%H:%M"),
                            "ต้องส่งก่อน": loc_data.get("ต้องส่งก่อน", "-") if i > 0 and i < len(r_indices)-1 else "-",
                            "ปริมาณนมที่ส่ง (L)": node_demand if i > 0 and i < len(r_indices)-1 else "-",
                            "นำทาง": maps_url if i > 0 else None,
                            "เวลาช่วงเดินทาง (นาที)": t_min if i > 0 else "-", 
                            "ระยะทางช่วง (กม.)": f"{l_dist:.2f}" if i > 0 else "-"
                        })
                        
                        if i < len(r_indices) - 1:
                            node_milk_volume = demands[n]
                            dyn_service_sec = (BASE_SERVICE_MIN * 60) + (node_milk_volume * PER_LITER_SEC) if i > 0 else 0
                            curr_time += timedelta(seconds=dyn_service_sec)
                    
                    line_text_summary += f"\n📦 โหลดนมขึ้นรถรวมทั้งสิ้น: *{v_loaded_milk} ลิตร*"
                    
                    st.caption(f"📦 โหลดนมจริงขึ้นรถคันนี้รวม: **{v_loaded_milk} L**")
                    df_schedule = pd.DataFrame(schedule)
                    st.dataframe(
                        df_schedule, use_container_width=True, hide_index=True, key=f"tbl_{v_idx}",
                        column_config={"นำทาง": st.column_config.LinkColumn("📍 นำทาง", display_text="เปิดแผนที่")}
                    )
                    
                    # 🌟 กล่องสําหรับให้ผู้ใช้กดคัดลอกเอาไปส่งต่อในกลุ่ม LINE ได้ทันทีไม่ต้องสลับหน้าจอไปมา
                    st.subheader("💬 ข้อความส่งไลน์สำหรับคนขับ (LINE Quick Share)")
                    st.text_area("ก๊อปปี้ข้อความด้านล่างนี้ ส่งเข้ากลุ่ม LINE คนขับรถคันนี้ได้ทันที", value=line_text_summary, height=140, key=f"line_txt_{v_idx}")
                    
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                        df_schedule.to_excel(writer, index=False, sheet_name=f'Vehicle_{v_idx+1}')
                    st.download_button(f"📥 ดาวน์โหลดใบงาน คันที่ {v_idx+1} (Excel)", buf.getvalue(), f"Ultimate_Plan_Vehicle_{v_idx+1}.xlsx", key=f"dl_{v_idx}", use_container_width=True)
    else:
        st.error("❌ ลอจิกโมเดลพัง: ข้อมูลขัดแย้งกันอย่างรวนเร หรือน้ำหนักสินค้าล้นเกินพิกัดกองรถที่มี")
