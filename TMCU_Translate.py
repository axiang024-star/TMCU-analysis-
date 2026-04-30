import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAME = 'Geely_TMCU_V1.1_20250513_PrivateCAN_10.dbc'
st.set_page_config(page_title="HVFAN 报文分析系统", layout="wide")

# 针对移动端兼容性的 CSS 补丁
st.markdown("""
    <style>
    /* 确保上传组件层级正确，防止在移动端被 UI 遮罩遮挡 */
    .stFileUploader {position: relative; z-index: 100;}
    /* 优化移动端文字显示 */
    @media (max-width: 768px) {
        .st-emotion-cache-16idsys p { font-size: 14px; }
        .stMarkdown h1 { font-size: 1.5rem !important; }
    }
    </style>
""", unsafe_allow_html=True)

# ===================== 2. 解析引擎 (保留原功能) =====================

@st.cache_resource
def load_dbc_engine(uploaded_file_content=None):
    """
    核心功能：加载 DBC 协议。支持 strict=False 以兼容非规范文件。
    """
    try:
        if uploaded_file_content is not None:
            # 解析上传的 DBC
            dbc_content = uploaded_file_content.decode('gbk', errors='ignore')
            return cantools.database.load_string(dbc_content, strict=False)
        elif os.path.exists(DBC_FILENAME):
            # 加载本地预设 DBC
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk', strict=False)
    except Exception as e:
        st.sidebar.error(f"DBC解析失败: {str(e)}")
    return None

def process_asc(file_content, db):
    """
    保留原有的 ASC 解析逻辑，支持 Vector 格式及 J1939 匹配
    """
    data_dict = {}
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x\s+(?:Rx|Tx)\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data or "Tx" in text_data: 
                break
        except: continue
            
    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                raw_id = int(m.group('id'), 16)
                hex_data = m.group('data').strip().replace(' ', '')
                raw_payload = bytearray.fromhex(hex_data)
                
                msg = None
                # 保留 ID 模糊匹配逻辑 (J1939)
                for search_id in [raw_id, raw_id & 0x1FFFFFFF, raw_id & 0x00FFFFFF]:
                    try:
                        msg = db.get_message_by_frame_id(search_id)
                        if msg: break
                    except KeyError: continue
                
                if not msg: continue
                
                if len(raw_payload) < msg.length:
                    raw_payload = raw_payload.ljust(msg.length, b'\x00')

                decoded = msg.decode(raw_payload, decode_choices=False)
                for s_n, s_v in decoded.items():
                    if not isinstance(s_v, (int, float)):
                        try: s_v = float(s_v)
                        except: continue

                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        sig_obj = msg.get_signal_by_name(s_n)
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': sig_obj.unit if sig_obj.unit else "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== 3. UI 交互逻辑 =====================
st.title("🚗 HVFAN 报文分析系统")

# 侧边栏：处理 DBC 加载
with st.sidebar:
    st.header("⚙️ 协议库设置")
    uploaded_dbc = st.file_uploader("手动上传 DBC 文件", type=['dbc'], key="dbc_loader_key")
    st.caption("提示：若云端环境找不到预设 DBC，请在此处直接上传。")

# 获取 DBC 字节流
dbc_bytes = uploaded_dbc.read() if uploaded_dbc else None
db = load_dbc_engine(dbc_bytes)

# 核心逻辑调整：即使没有 DB，也不要 st.stop()，保证页面组件能渲染
if not db:
    st.info("👈 请先在侧边栏上传 DBC 文件或确保预设 DBC 存在以激活分析功能。")
else:
    st.success(f"✅ DBC 解析成功：包含 {len(db.messages)} 条报文。")
    
    # ASC 上传：增加 key 保证移动端稳定性
    uploaded_asc = st.file_uploader("📂 上传 ASC 原始报文文件", type=['asc', 'txt'], key="asc_uploader_key")

    if uploaded_asc is not None:
        # 使用 session_state 缓存，避免手机端因 UI 刷新导致的重复解析
        file_key = f"data_{uploaded_asc.name}_{uploaded_asc.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在解析报文信号...'):
                content = uploaded_asc.read()
                st.session_state.full_data = process_asc(content, db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.get('full_data', {})

        if not full_data:
            st.warning("⚠️ 解析完成，但未在报文中找到符合 DBC 定义的信号数据。")
        else:
            # 交互控制面板 (保留原功能)
            with st.expander("🛠️ 信号显示设置", expanded=True):
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    all_sig_names = sorted(full_data.keys())
                    selected_sigs = st.multiselect("选择分析信号:", options=all_sig_names, default=all_sig_names[:1])
                with c2:
                    sync_on = st.toggle("🔗 同步缩放", value=True)
                with c3:
                    show_measure = st.toggle("📏 辅助线", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 保留原有的数据抽稀逻辑，优化移动端性能
                    if len(x) > 20000:
                        step = len(x) // 20000
                        x, y = x[::step], y[::step]
                    charts_to_render.append({"id": f"chart_{hash(name)}", "title": f"{name} ({d['unit']})", "x": x, "y": y})

                # --- Plotly 渲染逻辑 (保留同步缩放功能) ---
                js_logic = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-container"></div>
                <script>
                    const chartsData = {json.dumps(charts_to_render)};
                    const syncEnabled = {str(sync_on).lower()};
                    const hoverMode = "{'x unified' if show_measure else 'closest'}";
                    const chartIds = [];
                    let isRelayouting = false;

                    const container = document.getElementById('chart-container');
                    
                    chartsData.forEach((data) => {{
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '15px';
                        div.style.height = '350px';
                        container.appendChild(div);
                        chartIds.push(data.id);

                        const layout = {{
                            title: {{ text: data.title, font: {{ size: 13 }} }},
                            margin: {{ l: 60, r: 30, t: 40, b: 40 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot' }},
                            yaxis: {{ autorange: true }}
                        }};

                        Plotly.newPlot(data.id, [{{ x: data.x, y: data.y, type: 'scatter', mode: 'lines', line: {{ width: 1.5, color: '#2b6cb0' }} }}], layout, {{ responsive: true, displaylogo: false }});

                        if (syncEnabled) {{
                            document.getElementById(data.id).on('plotly_relayout', (eventData) => {{
                                if (isRelayouting) return;
                                isRelayouting = true;
                                const update = {{}};
                                if (eventData['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = eventData['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = eventData['xaxis.range[1]'];
                                }} else if (eventData['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}

                                if (Object.keys(update).length > 0) {{
                                    const promises = chartIds.map(id => {{
                                        if (id !== data.id) return Plotly.relayout(id, update);
                                    }});
                                    Promise.all(promises).then(() => {{ isRelayouting = false; }});
                                }} else {{
                                    isRelayouting = false;
                                }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 370 + 100
                components.html(js_logic, height=render_height, scrolling=False)
