import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置与移动端 UI 补丁 =====================
DBC_FILENAME = 'Geely_TMCU_V1.1_20250513_PrivateCAN_10.dbc'
st.set_page_config(page_title="HVFAN 报文分析系统", layout="wide")

# 针对移动端兼容性的深度 CSS 修复
st.markdown("""
    <style>
    /* 提升上传组件层级，确保在手机端可被点击，防止被隐形层遮挡 */
    .stFileUploader {
        position: relative; 
        z-index: 1000 !important; 
    }
    /* 增大手机端的点击热区，解决“选不中”感官问题 */
    section[data-testid="stFileUploadDropzone"] {
        padding: 2.5rem 1rem !important;
        border: 2px dashed #3498db !important;
        background-color: #f0f7ff !important;
        border-radius: 12px;
    }
    /* 适配手机窄屏字体 */
    @media (max-width: 768px) {
        .stMarkdown h1 { font-size: 1.3rem !important; }
        .st-emotion-cache-16idsys p { font-size: 14px !important; }
    }
    </style>
""", unsafe_allow_html=True)

# ===================== 2. 解析引擎 (完整保留原功能) =====================

@st.cache_resource
def load_dbc_engine(uploaded_file_content=None):
    try:
        if uploaded_file_content is not None:
            dbc_content = uploaded_file_content.decode('gbk', errors='ignore')
            return cantools.database.load_string(dbc_content, strict=False)
        elif os.path.exists(DBC_FILENAME):
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk', strict=False)
    except Exception as e:
        st.sidebar.error(f"DBC解析失败: {str(e)}")
    return None

def process_asc(file_content, db):
    data_dict = {}
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x\s+(?:Rx|Tx)\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
    # 尝试多种编码读取 ASC
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data or "Tx" in text_data: break
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
                
                # J1939 ID 模糊匹配逻辑
                msg = None
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
                            'unit': sig_obj.unit or "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== 3. UI 交互与逻辑集成 =====================

st.title("🚗 HVFAN 报文分析系统")

# 侧边栏：DBC 设置 (type=None 解决 iOS 无法选中问题)
with st.sidebar:
    st.header("⚙️ 协议库设置")
    uploaded_dbc = st.file_uploader("手动上传 DBC 文件", type=None, key="mobile_dbc_uploader")
    st.caption("提示：若文件置灰，请重命名并在末尾加 .txt")

# 加载 DBC
dbc_bytes = uploaded_dbc.read() if uploaded_dbc else None
db = load_dbc_engine(dbc_bytes)

if not db:
    st.info("👈 请先在侧边栏上传 DBC 文件或确保预设 DBC 存在以激活分析功能。")
    st.stop()

# ASC 上传：核心改动 type=None，解决截图中的文件置灰无法选中问题
uploaded_asc = st.file_uploader(
    "📂 上传 ASC 原始报文文件", 
    type=None, 
    key="mobile_asc_uploader"
)

if uploaded_asc is not None:
    # 后验后缀校验，确保业务逻辑正确
    fname = uploaded_asc.name.lower()
    if not (fname.endswith('.asc') or fname.endswith('.txt') or fname.endswith('.csv')):
        st.error(f"❌ 不支持的文件格式：{uploaded_asc.name}。请上传 .asc 或 .txt。")
    else:
        # 使用 session_state 缓存解析结果，防止信号删减操作导致重复解析，造成手机卡顿
        file_key = f"cache_{uploaded_asc.name}_{uploaded_asc.size}"
        if 'full_data' not in st.session_state or st.session_state.get('current_file_id') != file_key:
            with st.spinner('🔍 正在解析报文信号...'):
                content = uploaded_asc.read()
                st.session_state.full_data = process_asc(content, db)
                st.session_state.current_file_id = file_key
        
        full_data = st.session_state.get('full_data', {})

        if not full_data:
            st.warning("⚠️ 未在报文中找到符合 DBC 定义的信号。")
        else:
            # 交互控制面板：保留【信号删减、同步缩放、辅助线】
            with st.expander("🛠️ 信号显示与交互设置", expanded=True):
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    all_sig_names = sorted(full_data.keys())
                    # 信号的删减恢复主要靠 multiselect 驱动逻辑重绘
                    selected_sigs = st.multiselect("选择/删减分析信号:", options=all_sig_names, default=all_sig_names[:1])
                with c2:
                    sync_on = st.toggle("🔗 同步缩放", value=True)
                with c3:
                    show_measure = st.toggle("📏 辅助线", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 移动端性能优化：数据点抽稀
                    if len(x) > 15000:
                        step = len(x) // 15000
                        x, y = x[::step], y[::step]
                    charts_to_render.append({"id": f"chart_{hash(name)}", "title": f"{name} ({d['unit']})", "x": x, "y": y})

                # --- Plotly 渲染逻辑：完整保留所有交互功能 ---
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
                    
                    // 动态创建图表容器，实现信号删减恢复的实时渲染
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

                        // 同步缩放逻辑
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
                                }} else {{ isRelayouting = false; }}
                            }});
                        }}
                    }});
                </script>
                """
                # 动态计算 HTML 高度，确保信号增多时不会出现滚动条冲突
                render_height = len(selected_sigs) * 365 + 50
                components.html(js_logic, height=render_height, scrolling=False)
