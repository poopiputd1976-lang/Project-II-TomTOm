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

# ฟังก์ชันแปลงค่าปลอดภัย ป้องกันแอปพังจากข้อมูลพิมพ์ผิด
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
            
            target_types = ["ดีเซล", "แก๊สโซฮอล์ 91", "แก๊สโซฮอล์ 95"]
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

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Ultimate Milk Run Optimization", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางจัดส่งอัจฉริยะขั้นสุด (Ultimate Hybrid VRP)")
st.markdown("ระบบคำนวณกองรถ VRP ควบคุมความจุและเวลา พร้อมระบบเลือกเส้นทางอัจฉริยะ (Quickest / Shortest) ผ่าน TomTom API")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ราคาน้ำมัน Real-time")
    oil_data, update_date = fetch_today_oil_price()
    if oil_data:
        st.success(f"อัปเดตราคาล่าสุด: {update_date}")
        selected_oil = st.selectbox("เลือกชนิดน้ำมัน", list(oil_data.keys()))
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", value=float(oil_data[selected_oil]), step=0.5, format="%.2f")
    else:
        st.warning("⚠️ ไม่สามารถดึงข้อมูลราคา Real-time ได้ (ใช้ราคาประเมิน)")
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=35.0, step=0.5, format="%.2f")

    # ==========================================
    # 🚛 ย้ายขึ้นมาด้านบน: นโยบายและรูปแบบจัดเส้นทาง (Strategic Settings)
    # ==========================================
    st.header("🚛 ตั้งค่านโยบายและกองรถ")
    
    # ✨ ย้ายเมนูเลือกรูปแบบเส้นทางมาไว้บนสุดตามคำขอ เพื่อเพิ่มความโดดเด่นสะดุดตา
    ROUTE_TYPE = st.selectbox(
        "🛣️ รูปแบบการเลือกเส้นทางของระบบ", 
        ["fastest", "shortest"], 
        index=0, 
        format_func=lambda x: "⚡ Quickest (เน้นทางที่เร็วที่สุด เลี่ยงรถติด)" if x == "fastest" else "📏 Shortest (เน้นทางที่สั้นที่สุด เซฟระยะทางไมล์รถ)",
        help="Quickest จะยอมวิ่งอ้อมถนนใหญ่เพื่อให้ถึงไว ส่วน Shortest จะลัดตัดตรงลุยซอยแคบเพื่อให้ระยะทางกิโลเมตรน้อยที่สุด"
    )
    
    # ตัวเลือกโหมดกองรถ อยู่ต่อกันเป็นกลุ่มทางเลือกกลยุทธ์ธุรกิจ
    FLEET_MODE = st.radio(
        "🎯 โหมดการทำงานของกองรถ (Fleet Mode)",
        ["🟢 เน้นประหยัดต้นทุนที่สุด (Cost Saving)", "🔵 บังคับเฉลี่ยงานให้รถทุกคัน (Balanced Workload)"],
        index=0,
        help="โหมดประหยัดจะพยายามจอดรถไว้ให้มากที่สุด ส่วนโหมดเฉลี่ยงานจะบังคับให้รถทุกคันออกไปวิ่งเพื่อส่งเสร็จไว"
    )
    
    st.markdown("---")
    st.subheader("📦 ข้อมูลสเปครถยนต์ในกอง")
    
    # ➕ ➖ ปุ่มเพิ่มลดรถ
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
    AVOID_AREA = st.text_area("พิกัดพื้นที่ห้ามผ่าน (ขึ้นบรรทัดใหม่สำหรับกล่องถัดไป)", value="", height=80)
    st.caption("รูปแบบ: Lat,Long มุมที่ 1 : Lat,Long มุมที่ 2")

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
# 4. ประมวลผลคณิตศาสตร์ (Ultimate Optimization Engine)
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณโมเดลจำลองเส้นทางขั้นสูง", type="primary", use_container_width=True):
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
        
        # มิติเวลา (Time Dimension)
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            speed_kmh = 30 if ROUTE_TYPE == "fastest" else 25 
            return int((d / 1000) / speed_kmh * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
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

        # มิติความจุถังนม (Capacity Dimension)
        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        fleet_capacities = [v['capacity'] for v in vehicles_data]
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, fleet_capacities, True, "Capacity")

        # ตัวแปรค้นหาผลลัพธ์
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
                st.error(f"❌ TomTom API ปฏิเสธการจัดเส้นทางของรถคันที่ {v_idx+1}: {res.text}")
                st.stop()

        # --- Dashboard ---
        st.subheader("📊 บทวิเคราะห์ผลลัพธ์กองรถและเส้นทาง (KPI Dashboard)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ระยะทางวิ่งรวมกองรถ", f"{total_dist_km:.2f} กม.")
        c2.metric("งบประมาณค่าน้ำมันรวมทั้งหมด", f"฿{total_cost:.2f}")
        c3.metric("รูปแบบแผนที่นำทางที่ใช้", f"{'⚡ เร็วที่สุด (Quickest)' if ROUTE_TYPE == 'fastest' else '📏 สั้นที่สุด (Shortest)'}")
        hh, mm = divmod(total_time_sec // 60, 60)
        c4.metric("เวลารวมในภารกิจ", f"{int(hh)} ชม. {int(mm)} นาที" if hh > 0 else f"{int(mm)} นาที")

        # --- แผนที่และตารางคิวงาน ---
        col_map, col_table = st.columns([1.3, 1.7])
        with col_map:
            st.subheader("🗺️ แผนที่เส้นทางนำทางแยกสีคันรถ")
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

            if AVOID_AREA.strip() != "":
                for i, rect in enumerate(rectangles):
                    folium.Rectangle(
                        bounds=[[rect['southWestCorner']['latitude'], rect['southWestCorner']['longitude']], 
                                [rect['northEastCorner']['latitude'], rect['northEastCorner']['longitude']]],
                        color='#E74C3C', fill=True, fill_color='#E74C3C', fill_opacity=0.3, name=f'พื้นที่ห้ามผ่าน {i+1}'
                    ).add_to(m)

            folium.LayerControl().add_to(m)
            st_folium(m, width="100%", height=520, returned_objects=[])

        with col_table:
            st.subheader("📋 แผนการเดินรถรายวันแยกรายคันรถ")
            
            tabs = st.tabs([f"🚚 คันที่ {v_idx+1} ({res_data['config']['mode']})" for v_idx, res_data in fleet_results.items()])
            
            for tab_i, (v_idx, res_data) in enumerate(fleet_results.items()):
                with tabs[tab_i]:
                    st.markdown(f"**ระยะทางสะสมเที่ยวนี้:** {res_data['dist_km']:.2f} กม. | **ค่าน้ำมันคันนี้:** ฿{res_data['cost']:.2f} | **ความจุสูงสุดรถ:** {res_data['config']['capacity']} L")
                    
                    schedule = []
                    curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                    r_indices = res_data['indices']
                    v_loaded_milk = 0 
                    
                    for i in range(len(r_indices)):
                        n = r_indices[i]
                        t_min, l_dist, f_used = 0, 0.0, 0.0
                        loc_data = edited_df.iloc[n]
                        
                        if i > 0:
                            leg = res_data['route_data']['legs'][i-1]['summary']
                            t_min = math.ceil(leg['travelTimeInSeconds'] / 60)
                            l_dist = leg['lengthInMeters'] / 1000
                            f_used = l_dist / res_data['config']['km_l']
                            curr_time += timedelta(minutes=t_min)
                        
                        maps_url = f"https://www.google.com/maps/dir/?api=1&destination={loc_data['Lat']},{loc_data['Lon']}"
                        
                        if i == 0: display_name = f"{loc_data['ชื่อสถานที่']} (จุดสตาร์ท)"
                        elif i == len(r_indices) - 1: display_name = f"{loc_data['ชื่อสถานที่']} (กลับเข้าฟาร์ม)"
                        else: display_name = loc_data["ชื่อสถานที่"]
                        
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
                            curr_time += timedelta(seconds=SERVICE_TIME_SEC)
                    
                    st.caption(f"📦 โหลดนมจริงขึ้นรถคันนี้รวม: **{v_loaded_milk} L**")
                    df_schedule = pd.DataFrame(schedule)
                    st.dataframe(
                        df_schedule, use_container_width=True, hide_index=True, key=f"tbl_{v_idx}",
                        column_config={
                            "นำทาง": st.column_config.LinkColumn("📍 นำทาง", display_text="เปิดแผนที่")
                        }
                    )
                    
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                        df_schedule.to_excel(writer, index=False, sheet_name=f'Vehicle_{v_idx+1}')
                    st.download_button(f"📥 ดาวน์โหลดใบงาน คันที่ {v_idx+1} (Excel)", buf.getvalue(), f"Ultimate_Plan_Vehicle_{v_idx+1}.xlsx", key=f"dl_{v_idx}", use_container_width=True)
    else:
        st.error("❌ ลอจิกโมเดลพัง: ข้อมูลขัดแย้งกันอย่างรุนแรง หรือน้ำหนักสินค้าล้นเกินพิกัดกองรถที่มี")
