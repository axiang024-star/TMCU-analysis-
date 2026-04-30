import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAME = 'Geely_TMCU_V1.1_20250513_PrivateCAN_10.dbc'
st.set_page_config(page_title="HVFAN 报文分析系统", layout="wide")

# 针对移动端兼容性的深度 CSS 修复
st.markdown("""
    <style>
    /* 1. 强制提升上传组件层级，确保在手机端可被点击 */
    .stFileUploader {
        position: relative; 
        z-index: 1000 !important; 
    }
    /* 2. 增大手机端的点击热区 */
    section[data-testid="stFileUploadDropzone"] {
        padding: 2rem 1rem !important;
        border: 2px dashed #3498db !important;
        background-color: #f8f9fa !important;
    }
    /* 3. 移动端文字优化 */
    @media (max-width: 768px) {
        .stMarkdown h1 { font-size: 1.2rem !important; }
        .st-emotion-cache-16idsys p { font-size: 13px !important; }
    }
    </style>
""", unsafe_allow_html=True)

# ===================== 2. 解析引擎 =====================

@st.cache_resource
def load_dbc_engine(uploaded_file_content=None):
    try:
        if uploaded_file_content is not None:
            # 兼容处理上传的 DBC
            dbc_text = uploaded_file_content.decode('gbk', errors='ignore')
            return cantools.database.load_string(dbc_text, strict=False)
        elif os.path.exists(DBC_FILENAME):
            # 加载本地 DBC
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk', strict=False)
    except Exception as e:
        st.error(f"DBC 解析失败: {e}")
    return None

def process_asc(file_content, db):
    data_dict = {}
    # 增强型正则：匹配标准 Vector ASC 格式
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x\s+(?:Rx|Tx)\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
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
                
                # ID 模糊匹配 (处理 J1939 优先级差异)
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
                            'unit': sig_obj.unit if sig_obj.unit else "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== 3. 页面布局 =====================
st.title("🚗 HVFAN 移动端增强版分析系统")

# --- 关键修复：上传组件直接置于顶层，不放入任何 If 块中 ---
uploaded_asc = st.file_uploader(
    "📂 第一步：上传 ASC 报文文件", 
    type=['asc', 'txt'], 
    key="mobile_asc_uploader"
)

# 侧边栏处理 DBC
with st.sidebar:
    st.header("⚙️ 协议库配置")
    uploaded_dbc = st.file_uploader("手动更新 DBC", type=['dbc'], key="mobile_dbc_uploader")
    st.info("手机端建议：若自动加载失败，请手动上传一次 DBC。")

# 加载 DBC 引擎
dbc_bytes = uploaded_dbc.read() if uploaded_dbc else None
db = load_dbc_engine(dbc_bytes)

# --- 逻辑检查点 ---
if not db:
    st.warning("⚠️ 协议库未就绪。请确认侧边栏 DBC 文件状态。")
    st.stop()

if uploaded_asc is not None:
    # 状态持久化，防止手机切换浏览器导致的重置
    file_key = f"cache_{uploaded_asc.name}_{uploaded_asc.size}"
    if 'data_cache' not in st.session_state or st.session_state.current_file_id != file_key:
        with st.spinner('⏳ 正在解析大规模报文...'):
            content = uploaded_asc.read()
            st.session_state.data_cache = process_asc(content, db)
            st.session_state.current_file_id = file_key
    
    full_data = st.session_state.data_cache

    if not full_data:
        st.error("❌ 解析失败：文件中未发现与当前 DBC 匹配的 ID。")
    else:
        st.success(f"📈 解析成功！识别到 {len(full_data)} 个信号")

        # 交互面板
        with st.expander("🛠️ 信号过滤与显示设置", expanded=True):
            c1, c2 = st.columns([2, 1])
            with c1:
                all_sigs = sorted(full_data.keys())
                selected_sigs = st.multiselect("选择分析信号", all_sigs, default=all_sigs[:1])
            with c2:
                sync_on = st.toggle("同步缩放", value=True)
                show_measure = st.toggle("开启测量轴", value=True)

        if selected_sigs:
            charts_json = []
            for name in selected_sigs:
                d = full_data[name]
                x, y = d['x'], d['y']
                # 手机端强制抽稀：保证流畅度
                if len(x) > 15000:
                    step = len(x) // 15000
                    x, y = x[::step], y[::step]
                charts_json.append({"id": f"ch_{hash(name)}", "title": f"{name} ({d['unit']})", "x": x, "y": y})

            # Plotly 渲染引擎 (兼容手机触摸)
            js_code = f"""
            <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
            <div id="chart-box"></div>
            <script>
                const dataSet = {json.dumps(charts_json)};
                const sync = {str(sync_on).lower()};
                const container = document.getElementById('chart-box');
                const chartIds = [];
                let relayouting = false;

                dataSet.forEach(data => {{
                    const d = document.createElement('div');
                    d.id = data.id;
                    d.style.marginBottom = '20px';
                    d.style.height = '320px';
                    container.appendChild(d);
                    chartIds.push(data.id);

                    const layout = {{
                        title: {{ text: data.title, font: {{ size: 14 }} }},
                        margin: {{ l: 50, r: 20, t: 40, b: 40 }},
                        template: 'plotly_white',
                        hovermode: "{'x unified' if show_measure else 'closest'}",
                        xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot', spikecolor: '#999' }},
                        yaxis: {{ autorange: true }}
                    }};

                    Plotly.newPlot(data.id, [{{ x: data.x, y: data.y, type: 'scatter', mode: 'lines', line: {{ width: 2, color: '#1f77b4' }} }}], layout, {{ responsive: true, displaylogo: false, scrollZoom: true }});

                    if (sync) {{
                        document.getElementById(data.id).on('plotly_relayout', (ed) => {{
                            if (relayouting) return;
                            relayouting = true;
                            const up = {{}};
                            if (ed['xaxis.range[0]']) {{
                                up['xaxis.range[0]'] = ed['xaxis.range[0]'];
                                up['xaxis.range[1]'] = ed['xaxis.range[1]'];
                            }} else if (ed['xaxis.autorange']) {{
                                up['xaxis.autorange'] = true;
                            }}
                            if (Object.keys(up).length > 0) {{
                                const ps = chartIds.map(id => id !== data.id ? Plotly.relayout(id, up) : null);
                                Promise.all(ps).then(() => relayouting = false);
                            }} else relayouting = false;
                        }});
                    }}
                }});
            </script>
            """
            components.html(js_code, height=len(selected_sigs)*350 + 50, scrolling=False)
