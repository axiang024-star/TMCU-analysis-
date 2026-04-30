import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置与移动端 UI 补丁 =====================
DBC_FILENAME = 'Geely_TMCU_V1.1_20250513_PrivateCAN_10.dbc'
st.set_page_config(page_title="HVFAN 报文分析系统", layout="wide")

# 应用【方案一】：深度 CSS 修复，解决手机端点击穿透和热区过小问题
st.markdown("""
    <style>
    /* 提升上传组件层级，防止被 Streamlit 默认的隐形 Overlay 遮挡 */
    .stFileUploader {
        position: relative; 
        z-index: 1000 !important; 
    }
    /* 增大手机端的点击热区，方便手指操作 */
    section[data-testid="stFileUploadDropzone"] {
        padding: 2.5rem 1rem !important;
        border: 2px dashed #3498db !important;
        background-color: #f0f7ff !important;
        border-radius: 10px;
    }
    /* 针对窄屏手机优化字体，防止 UI 溢出 */
    @media (max-width: 768px) {
        .stMarkdown h1 { font-size: 1.3rem !important; }
        .st-emotion-cache-16idsys p { font-size: 14px !important; }
        .stButton button { width: 100%; } /* 按钮全屏宽度 */
    }
    </style>
""", unsafe_allow_html=True)

# ===================== 2. 高性能解析引擎 =====================

@st.cache_resource
def load_dbc_engine(uploaded_file_content=None):
    """缓存 DBC 解析结果，避免每次操作都重新解析协议文件"""
    try:
        if uploaded_file_content is not None:
            dbc_text = uploaded_file_content.decode('gbk', errors='ignore')
            return cantools.database.load_string(dbc_text, strict=False)
        elif os.path.exists(DBC_FILENAME):
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk', strict=False)
    except Exception as e:
        st.error(f"DBC 解析失败: {e}")
    return None

def process_asc(file_content, db):
    """解析 ASC 逻辑，保持不变"""
    data_dict = {}
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x\s+(?:Rx|Tx)\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
    # 自动识别编码
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
                
                # J1939 ID 兼容逻辑
                msg = None
                for search_id in [raw_id, raw_id & 0x1FFFFFFF, raw_id & 0x00FFFFFF]:
                    try:
                        msg = db.get_message_by_frame_id(search_id)
                        if msg: break
                    except: continue
                
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
                        data_dict[full_n] = {'x': [], 'y': [], 'unit': sig_obj.unit or "", 'label': s_n}
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== 3. 业务逻辑与持久化 =====================

# 侧边栏：配置区
with st.sidebar:
    st.header("⚙️ 协议库配置")
    # 应用【方案三】：指定 key 确保组件 ID 稳定
    uploaded_dbc = st.file_uploader("手动上传 DBC", type=['dbc'], key="dbc_loader_mobile")
    st.caption("注：手机端若自动加载失败，请手动上传。")

db = load_dbc_engine(uploaded_dbc.read() if uploaded_dbc else None)

# 主界面
if not db:
    st.warning("👈 请先确保协议库 (DBC) 已加载")
    st.stop()

# 应用【方案三】：上传组件置于顶层，不受逻辑分支干扰
uploaded_asc = st.file_uploader(
    "📂 第一步：上传 ASC 报文文件", 
    type=['asc', 'txt'], 
    key="asc_loader_mobile"
)

if uploaded_asc is not None:
    # 应用【方案二】：使用 Session State 状态锁，防止手机端因内存回收导致的重复解析
    file_id = f"cache_{uploaded_asc.name}_{uploaded_asc.size}"
    
    if 'main_data' not in st.session_state or st.session_state.get('last_file_id') != file_id:
        with st.spinner('⏳ 正在解析大规模报文...'):
            content = uploaded_asc.read()
            st.session_state.main_data = process_asc(content, db)
            st.session_state.last_file_id = file_id
    
    full_data = st.session_state.main_data

    if not full_data:
        st.error("❌ 未匹配到数据")
    else:
        # 应用【方案四】：强制抽稀，保证手机端 Plotly 图表不卡死
        with st.expander("🛠️ 信号显示设置", expanded=True):
            selected_sigs = st.multiselect("选择分析信号", sorted(full_data.keys()), default=sorted(full_data.keys())[:1])
            sync_on = st.toggle("同步缩放", value=True)

        if selected_sigs:
            charts_to_draw = []
            for name in selected_sigs:
                d = full_data[name]
                x, y = d['x'], d['y']
                
                # 移动端性能警戒线：超过 15,000 点强制抽稀
                if len(x) > 15000:
                    step = len(x) // 15000
                    x, y = x[::step], y[::step]
                
                charts_to_draw.append({"id": f"c_{hash(name)}", "title": f"{name} ({d['unit']})", "x": x, "y": y})

            # Plotly 渲染 (高度自适应移动端)
            js_engine = f"""
            <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
            <div id="viz-container"></div>
            <script>
                const charts = {json.dumps(charts_to_draw)};
                const isSync = {str(sync_on).lower()};
                const container = document.getElementById('viz-container');
                const ids = [];
                let isLock = false;

                charts.forEach(data => {{
                    const div = document.createElement('div');
                    div.id = data.id;
                    div.style.height = '320px'; // 针对手机屏幕调整高度
                    div.style.marginBottom = '15px';
                    container.appendChild(div);
                    ids.push(data.id);

                    const layout = {{
                        title: {{ text: data.title, font: {{ size: 14 }} }},
                        margin: {{ l: 50, r: 20, t: 40, b: 40 }},
                        template: 'plotly_white',
                        xaxis: {{ showspikes: true, spikemode: 'across' }},
                        yaxis: {{ autorange: true }}
                    }};

                    Plotly.newPlot(data.id, [{{ x: data.x, y: data.y, mode: 'lines', line: {{ width: 2 }} }}], layout, {{ responsive: true, displaylogo: false }});

                    if (isSync) {{
                        document.getElementById(data.id).on('plotly_relayout', (e) => {{
                            if (isLock) return;
                            isLock = true;
                            const update = {{}};
                            if (e['xaxis.range[0]']) {{
                                update['xaxis.range[0]'] = e['xaxis.range[0]'];
                                update['xaxis.range[1]'] = e['xaxis.range[1]'];
                            }}
                            ids.forEach(id => {{ if(id !== data.id) Plotly.relayout(id, update); }});
                            isLock = false;
                        }});
                    }}
                }});
            </script>
            """
            components.html(js_engine, height=len(selected_sigs)*350 + 50)
