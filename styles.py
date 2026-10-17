"""styles.py — design system shared with the Ratio Analyzer (light/dark tokens, header, stepper, cards)."""

CSS = r"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

/* ── Design tokens — Light mode ──────────────── */
:root {
    --navy:        #0F172A;
    --navy-mid:    #1E293B;
    --navy-light:  #334155;
    --indigo:      #6366F1;
    --indigo-dark: #4F46E5;
    --teal:        #14B8A6;
    --teal-dark:   #0D9488;
    --amber:       #F59E0B;
    --red:         #EF4444;
    --green:       #10B981;

    /* Adaptive tokens */
    --bg-page:       #F8FAFC;
    --bg-card:       #FFFFFF;
    --bg-subtle:     #F1F5F9;
    --bg-input:      #FFFFFF;
    --border:        #E2E8F0;
    --border-mid:    #CBD5E1;
    --text-primary:  #0F172A;
    --text-secondary:#475569;
    --text-muted:    #64748B;
    --text-inverse:  #FFFFFF;
    --shadow-sm:  0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md:  0 4px 12px rgba(0,0,0,.08), 0 2px 4px rgba(0,0,0,.04);
    --shadow-lg:  0 12px 28px rgba(0,0,0,.10), 0 4px 8px rgba(0,0,0,.04);
    --radius-sm: 6px; --radius-md: 10px; --radius-lg: 16px; --radius-xl: 22px;
    /* Alert backgrounds */
    --alert-success-bg: #f0fdf4; --alert-success-border: #10B981; --alert-success-text: #065f46;
    --alert-info-bg:    #eef2ff; --alert-info-border:    #6366F1; --alert-info-text:    #3730a3;
    --alert-warning-bg: #fffbeb; --alert-warning-border: #F59E0B; --alert-warning-text: #92400e;
    --alert-error-bg:   #fef2f2; --alert-error-border:   #EF4444; --alert-error-text:   #991b1b;
    /* Banner, ratio description, export cards */
    --ratio-banner-bg:     #eef2ff;
    --ratio-banner-border: #c7d2fe;
    --ratio-banner-text:   #3730a3;
    --export-card-bg:      #f8fafc;
    --export-card-border:  #e2e8f0;
    --export-card-title:   #334155;
    --export-card-body:    #64748b;
    --empty-state-text:    #64748B;
    --empty-state-head:    #334155;
    --breadcrumb-cat:      #64748B;
    --breadcrumb-arrow:    #CBD5E1;
    --breadcrumb-title:    #1E293B;
    --prog-label:          #334155;
}

/* ── Dark mode token overrides ───────────────── */
[data-theme="dark"], .stApp[data-theme="dark"] {
    --bg-page:       #0F172A;
    --bg-card:       #1E293B;
    --bg-subtle:     #162032;
    --bg-input:      #1E293B;
    --border:        #334155;
    --border-mid:    #475569;
    --text-primary:  #F1F5F9;
    --text-secondary:#CBD5E1;
    --text-muted:    #64748B;
    /* Alert backgrounds — slightly brighter borders so they're visible on dark bg */
    --alert-success-bg: rgba(16,185,129,.12); --alert-success-border: #34D399; --alert-success-text: #6EE7B7;
    --alert-info-bg:    rgba(99,102,241,.15); --alert-info-border:    #818CF8; --alert-info-text:    #C7D2FE;
    --alert-warning-bg: rgba(245,158,11,.12); --alert-warning-border: #FCD34D; --alert-warning-text: #FDE68A;
    --alert-error-bg:   rgba(239,68,68,.12);  --alert-error-border:   #F87171; --alert-error-text:   #FECACA;
    --ratio-banner-bg:     rgba(99,102,241,.15);
    --ratio-banner-border: rgba(99,102,241,.4);
    --ratio-banner-text:   #C7D2FE;
    --export-card-bg:      #1E293B;
    --export-card-border:  #334155;
    --export-card-title:   #E2E8F0;
    --export-card-body:    #94A3B8;
    --empty-state-text:    #64748B;
    --empty-state-head:    #CBD5E1;
    --breadcrumb-cat:      #64748B;
    --breadcrumb-arrow:    #475569;
    --breadcrumb-title:    #F1F5F9;
    --prog-label:          #E2E8F0;
}


/* ── Base ─────────────────────────────────────── */
html, body, [class*="css"] { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }
.main .block-container { padding-top: 0 !important; padding-bottom: 3rem; max-width: 1480px; }
#MainMenu, footer { visibility: hidden; }

/* ── Header banner ────────────────────────────── */
.app-header {
    background: linear-gradient(135deg, #0F172A 0%, #1E293B 50%, #0F2845 100%);
    padding: 2rem 2.5rem;
    border-radius: 0 0 var(--radius-xl) var(--radius-xl);
    margin: -1rem -1rem 1.5rem -1rem;
    position: relative; overflow: hidden;
    box-shadow: var(--shadow-lg);
}
.app-header::before {
    content: ''; position: absolute; inset: 0;
    background-image: linear-gradient(rgba(99,102,241,.12) 1px,transparent 1px),
                      linear-gradient(90deg,rgba(99,102,241,.12) 1px,transparent 1px);
    background-size: 32px 32px;
}
.app-header::after {
    content: ''; position: absolute; top:-60px; right:-60px;
    width:260px; height:260px;
    background: radial-gradient(circle,rgba(99,102,241,.25) 0%,transparent 70%);
    pointer-events: none;
}
.header-inner { position:relative; z-index:1; display:flex; align-items:center; gap:1.5rem; }
.header-icon {
    width:56px; height:56px;
    background: linear-gradient(135deg,var(--indigo) 0%,var(--teal) 100%);
    border-radius: var(--radius-md); display:flex; align-items:center; justify-content:center;
    font-size:1.75rem; box-shadow:0 4px 14px rgba(99,102,241,.4); flex-shrink:0;
}
.header-text h1 { font-size:1.7rem; font-weight:800; color:white; margin:0; letter-spacing:-.02em; line-height:1.2; }
.header-text p  { color:rgba(255,255,255,.65); font-size:.88rem; margin:.2rem 0 0; }
.header-badges  { margin-left:auto; display:flex; gap:.5rem; align-items:center; }
.badge { padding:.25rem .75rem; border-radius:20px; font-size:.72rem; font-weight:600; letter-spacing:.03em; white-space:nowrap; }
.badge-review { background:rgba(255,255,255,.08); color:#E2E8F0; border:1px solid rgba(255,255,255,.18); }
.badge-live    { background:rgba(16,185,129,.2); color:#34D399; border:1px solid rgba(16,185,129,.3); }
.badge-live::before { content:'● '; }
.badge-version { background:rgba(99,102,241,.2); color:#A5B4FC; border:1px solid rgba(99,102,241,.3); }

/* ── Workflow stepper ─────────────────────────── */
.stepper {
    display:flex; align-items:center; padding:1rem 1.5rem;
    background: var(--bg-card); border-radius: var(--radius-lg);
    border:1px solid var(--border); box-shadow: var(--shadow-sm);
    margin-bottom:1.25rem;
}
.step-node { display:flex; align-items:center; flex:1; }
.step-circle {
    width:36px; height:36px; border-radius:50%;
    display:flex; align-items:center; justify-content:center;
    font-size:.85rem; font-weight:700; flex-shrink:0; transition:all .25s;
}
.step-circle.done   { background:linear-gradient(135deg,#10B981,#059669); color:white; box-shadow:0 3px 10px rgba(16,185,129,.35); }
.step-circle.active { background:linear-gradient(135deg,var(--indigo),var(--indigo-dark)); color:white; box-shadow:0 3px 10px rgba(99,102,241,.4); }
.step-circle.pending{ background:var(--bg-subtle); color:var(--text-muted); border:2px solid var(--border); }
.step-label { margin-left:.6rem; }
.step-label .step-name { font-weight:600; font-size:.85rem; color:var(--text-secondary); }
.step-label .step-desc { font-size:.73rem; color:var(--text-muted); }
.step-label .step-name.active-text { color:var(--indigo); }
.step-label .step-name.done-text   { color:#059669; }
.step-connector { flex:1; height:2px; max-width:80px; background:var(--border); margin:0 .5rem; }
.step-connector.done { background:linear-gradient(90deg,#10B981,#059669); }

/* ── Alerts ───────────────────────────────────── */
.alert-success, .alert-info, .alert-warning, .alert-error {
    padding:.85rem 1.1rem; border-radius: var(--radius-md); border-left:4px solid;
    margin:.75rem 0; font-size:.9rem; animation:slideIn .25s ease-out;
}
@keyframes slideIn { from{opacity:0;transform:translateY(-8px)} to{opacity:1;transform:translateY(0)} }
.alert-success { background:var(--alert-success-bg); border-color:var(--alert-success-border); color:var(--alert-success-text); }
.alert-info    { background:var(--alert-info-bg);    border-color:var(--alert-info-border);    color:var(--alert-info-text); }
.alert-warning { background:var(--alert-warning-bg); border-color:var(--alert-warning-border); color:var(--alert-warning-text); }
.alert-error   { background:var(--alert-error-bg);   border-color:var(--alert-error-border);   color:var(--alert-error-text); }

/* ── Section title ────────────────────────────── */
.section-title {
    font-size:1.15rem; font-weight:700; color:var(--text-primary);
    padding-bottom:.6rem; border-bottom:2px solid var(--indigo);
    margin:1.25rem 0 1rem; display:flex; align-items:center; gap:.5rem;
}

/* ── Mapping category badge ───────────────────── */
.cat-badge {
    display:inline-flex; align-items:center; gap:.35rem;
    padding:.22rem .65rem; border-radius:20px;
    font-size:.75rem; font-weight:600;
    background:var(--bg-subtle); color:var(--text-secondary); border:1px solid var(--border);
    margin-bottom:.6rem;
}
.cat-badge.complete { background:rgba(16,185,129,.12); color:#059669; border-color:rgba(16,185,129,.3); }
.cat-badge.partial  { background:rgba(245,158,11,.12);  color:#D97706; border-color:rgba(245,158,11,.3); }

/* ── Progress bar ─────────────────────────────── */
.prog-bar-wrap { background:var(--border); border-radius:4px; height:6px; overflow:hidden; }
.prog-bar-fill { height:100%; border-radius:4px; transition:width .4s ease;
    background:linear-gradient(90deg,var(--indigo),var(--teal)); }

/* ── Ratio info banner ────────────────────────── */
.ratio-banner {
    background:var(--ratio-banner-bg); border:1px solid var(--ratio-banner-border);
    border-radius:10px; padding:.75rem 1rem; margin-bottom:1rem;
    font-size:.87rem; color:var(--ratio-banner-text);
}
/* ── Breadcrumb ───────────────────────────────── */
.breadcrumb {
    display:flex; align-items:center; gap:.5rem; margin-bottom:.5rem;
}
.breadcrumb-cat   { font-size:.75rem; color:var(--breadcrumb-cat); font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
.breadcrumb-arrow { color:var(--breadcrumb-arrow); }
.breadcrumb-title { font-size:1.1rem; font-weight:700; color:var(--breadcrumb-title); }

/* ── Export cards ─────────────────────────────── */
.export-card {
    background:var(--export-card-bg); border:1px solid var(--export-card-border);
    border-radius:12px; padding:1.1rem; min-height:196px;
}
.export-card-title { font-weight:700; color:var(--export-card-title); margin-bottom:.3rem; }
.export-card-body  { font-size:.82rem; color:var(--export-card-body); margin-bottom:.8rem; }

/* ── Empty states ─────────────────────────────── */
.empty-state { text-align:center; padding:3rem; color:var(--empty-state-text); }
.empty-state-icon { font-size:3rem; margin-bottom:1rem; }
.empty-state h3 { color:var(--empty-state-head); }

/* ── Streamlit native overrides ───────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap:.35rem; background:var(--bg-subtle); padding:.4rem;
    border-radius:var(--radius-lg); border:1px solid var(--border);
}
.stTabs [data-baseweb="tab"] {
    border-radius:var(--radius-md); padding:.6rem 1.25rem;
    font-weight:600; font-size:.88rem; color:var(--text-secondary) !important; transition:all .2s;
}
.stTabs [data-baseweb="tab"]:hover { background:var(--bg-card); color:var(--indigo) !important; }
.stTabs [aria-selected="true"] {
    background:linear-gradient(135deg,var(--indigo) 0%,var(--indigo-dark) 100%) !important;
    color:white !important; box-shadow:var(--shadow-md);
}
[data-testid="stMetric"] {
    background:var(--bg-card); border:1px solid var(--border);
    border-radius:var(--radius-md); padding:.9rem 1rem;
    box-shadow:var(--shadow-sm); transition:all .2s;
}
[data-testid="stMetric"]:hover { box-shadow:var(--shadow-md); transform:translateY(-2px); }
[data-testid="stMetricLabel"] { font-size:.78rem !important; font-weight:600; color:var(--text-muted) !important; text-transform:uppercase; letter-spacing:.04em; }
[data-testid="stMetricValue"] { font-size:1.45rem !important; font-weight:800; color:var(--text-primary) !important; }
.stButton button {
    border-radius:var(--radius-md); font-weight:600; font-size:.88rem;
    padding:.45rem 1.1rem; transition:all .2s; border:none;
}
.stButton button:hover { transform:translateY(-1px); box-shadow:var(--shadow-md); }
.stButton button[kind="primary"] {
    background:linear-gradient(135deg,var(--indigo) 0%,var(--indigo-dark) 100%); color:white;
}
.stButton button[kind="primary"]:hover {
    background:linear-gradient(135deg,#818cf8 0%,var(--indigo) 100%);
}
.stDownloadButton button {
    background:linear-gradient(135deg,var(--teal) 0%,var(--teal-dark) 100%);
    color:white; border:none; border-radius:var(--radius-md); font-weight:600; transition:all .2s;
}
.stDownloadButton button:hover {
    background:linear-gradient(135deg,#2dd4bf 0%,var(--teal) 100%);
    transform:translateY(-1px); box-shadow:var(--shadow-md);
}
.stDataFrame { border-radius:var(--radius-md); overflow:hidden; box-shadow:var(--shadow-md); }
[data-testid="stFileUploader"] {
    background:var(--bg-subtle); border:2px dashed var(--border-mid);
    border-radius:var(--radius-lg); padding:.75rem; transition:all .2s;
}
[data-testid="stFileUploader"]:hover { border-color:var(--indigo); background:rgba(99,102,241,.05); }
.streamlit-expanderHeader {
    font-weight:600 !important; font-size:.9rem !important;
    background:var(--bg-subtle) !important; border-radius:var(--radius-md) !important;
    color:var(--text-secondary) !important;
}
.streamlit-expanderHeader:hover { background:var(--bg-card) !important; color:var(--indigo) !important; }

/* ── Sidebar — always dark ────────────────────── */
section[data-testid="stSidebar"] { background:var(--navy) !important; border-right:1px solid var(--navy-light) !important; }
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p { color:rgba(255,255,255,.8) !important; }
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 { color:white !important; }
section[data-testid="stSidebar"] [data-testid="stMetric"]      { background:var(--navy-mid) !important; border-color:var(--navy-light) !important; }
section[data-testid="stSidebar"] [data-testid="stMetricLabel"] { color:rgba(255,255,255,.5) !important; }
section[data-testid="stSidebar"] [data-testid="stMetricValue"] { color:white !important; }
section[data-testid="stSidebar"] .stSelectbox > div > div,
section[data-testid="stSidebar"] .stMultiSelect > div > div   { background:var(--navy-mid) !important; border-color:var(--navy-light) !important; color:white !important; }
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stRadio label { color:rgba(255,255,255,.8) !important; }
.sidebar-divider { border-top:1px solid rgba(255,255,255,.1); margin:.75rem 0; }
.sidebar-section-label { font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em; color:rgba(255,255,255,.35) !important; padding:.4rem 0 .1rem; }

/* ── App footer ───────────────────────────────── */
.app-footer { text-align:center; color:var(--text-muted); padding:1.5rem 0; margin-top:2rem; border-top:1px solid var(--border); font-size:.8rem; }
.app-footer strong { color:var(--indigo); }

@media (max-width:768px) {
    .app-header { padding:1.25rem; border-radius:0 0 var(--radius-lg) var(--radius-lg); }
    .header-text h1 { font-size:1.25rem; }
    .header-badges { display:none; }
}
@keyframes fadeIn { from{opacity:0} to{opacity:1} }
/* ── Flux Studio additions ────────────────────── */
section[data-testid="stSidebar"] [data-baseweb="select"] * { color:#F1F5F9 !important; }
section[data-testid="stSidebar"] [data-baseweb="select"] > div { background:var(--navy-mid) !important; border-color:var(--navy-light) !important; }
.stButton button[kind="secondary"] { background:var(--bg-card); color:var(--text-primary); border:1px solid var(--border-mid); }
.stButton button[kind="secondary"]:hover { border-color:var(--indigo); color:var(--indigo); }
.range-box { background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-md); padding:.6rem 1rem; margin:.35rem 0 .7rem; font-size:.85rem; color:var(--text-secondary); }
.range-box b { color:var(--text-primary); }
.kpi-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; margin:.25rem 0 1rem; }
@media (max-width:900px){ .kpi-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
.kpi { background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-md); padding:.85rem 1rem; box-shadow:var(--shadow-sm); }
.kpi .kl { font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--text-muted); }
.kpi .kv { font-size:1.45rem; font-weight:800; color:var(--text-primary); margin-top:.15rem; }
.kpi .ks { font-size:.76rem; color:var(--text-secondary); margin-top:.1rem; }
.kpi.red { border-left:4px solid var(--red); } .kpi.amber { border-left:4px solid var(--amber); }
.kpi.green { border-left:4px solid var(--green); } .kpi.indigo { border-left:4px solid var(--indigo); } .kpi.teal { border-left:4px solid var(--teal); }
.pill { display:inline-block; padding:.12rem .55rem; border-radius:12px; font-size:.72rem; font-weight:700; }
.pill.High { background:rgba(239,68,68,.14); color:#B91C1C; } .pill.Medium { background:rgba(245,158,11,.16); color:#B45309; }
.pill.Low { background:rgba(16,185,129,.15); color:#047857; } .pill.None { background:var(--bg-subtle); color:var(--text-muted); }
.rule-chip { display:inline-block; margin:.1rem .25rem .1rem 0; padding:.12rem .5rem; border-radius:8px; font-size:.72rem; font-weight:600;
    background:var(--bg-subtle); color:var(--text-secondary); border:1px solid var(--border); }
.draft-box { background:var(--ratio-banner-bg); border:1px solid var(--ratio-banner-border); color:var(--ratio-banner-text);
    border-radius:10px; padding:.8rem 1rem; font-size:.88rem; line-height:1.5; }
.how-card { background:var(--bg-card); border:1px solid var(--border); border-radius:12px; padding:1rem 1.1rem; height:100%; }
.how-card h4 { margin:.1rem 0 .35rem; font-size:.95rem; color:var(--text-primary); }
.how-card p { margin:0; font-size:.83rem; color:var(--text-secondary); line-height:1.5; }
.how-num { display:inline-flex; width:26px; height:26px; border-radius:50%; align-items:center; justify-content:center;
    background:linear-gradient(135deg,var(--indigo),var(--teal)); color:white; font-weight:700; font-size:.8rem; }

/* ── Round-3 polish ───────────────────────────── */
[data-testid="stSidebar"] [data-baseweb="select"] div,
[data-testid="stSidebar"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-baseweb="select"] input { color:#F1F5F9 !important; -webkit-text-fill-color:#F1F5F9 !important; opacity:1 !important; }
[data-testid="stSidebar"] [data-baseweb="select"] svg { fill:#CBD5E1 !important; }
.kpi-grid.two { grid-template-columns:repeat(2,minmax(0,1fr)); gap:.6rem; }
.kpi-grid.two .kv { font-size:1.15rem; }
.kpi .kv { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.seg-wrap { margin:.2rem 0 1rem; }
.seg-bar { display:flex; height:12px; border-radius:6px; overflow:hidden; background:#E2E8F0; }
.seg-bar div { height:100%; }
.seg-legend { display:flex; gap:1rem; flex-wrap:wrap; font-size:.78rem; color:var(--text-secondary); margin-top:.4rem; }
.seg-legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:.35rem; vertical-align:-1px; }
.seg-title { font-size:.85rem; font-weight:600; color:var(--text-secondary); margin-bottom:.35rem; }
@media (max-width:640px) {
  .stepper { padding:.7rem .8rem; }
  .step-label .step-desc { display:none; }
  .step-label .step-name { font-size:.75rem; white-space:nowrap; }
  .step-circle { width:28px; height:28px; font-size:.75rem; }
  .step-connector { margin:0 .25rem; }
  .kpi .kv { font-size:1.15rem; }
}
/* ── Tie-out strip and journal lines ─────────── */
.tieout { display:flex; flex-wrap:wrap; gap:.5rem; margin:.1rem 0 .9rem; }
.tie { flex:1 1 260px; display:flex; align-items:center; gap:.55rem; padding:.55rem .85rem; border-radius:var(--radius-md);
       font-size:.82rem; border:1px solid; }
.tie span { display:inline-flex; align-items:center; justify-content:center; width:1.35rem; height:1.35rem; border-radius:50%;
            font-weight:800; font-size:.8rem; flex:0 0 auto; }
.tie.ok { background:var(--alert-success-bg); border-color:var(--alert-success-border); color:var(--alert-success-text); }
.tie.ok span { background:var(--alert-success-border); color:#fff; }
.tie.bad { background:var(--alert-error-bg); border-color:var(--alert-error-border); color:var(--alert-error-text); }
.tie.bad span { background:var(--alert-error-border); color:#fff; }
.tie b { font-weight:800; }
/* Streamlit ≥1.5x selectboxes are react-aria combo boxes */
[data-testid="stSidebar"] .react-aria-ComboBox [role="group"] { background:var(--navy-mid) !important; border:1px solid var(--navy-light) !important; }
[data-testid="stSidebar"] .react-aria-ComboBox input { color:#F1F5F9 !important; -webkit-text-fill-color:#F1F5F9 !important; opacity:1 !important; background:transparent !important; }
[data-testid="stSidebar"] .react-aria-ComboBox button svg { fill:#CBD5E1 !important; color:#CBD5E1 !important; }
[data-testid="stSidebar"] [data-testid="stNumberInput"] input { color:#F1F5F9 !important; -webkit-text-fill-color:#F1F5F9 !important; background:var(--navy-mid) !important; }
[data-testid="stSidebar"] [data-testid="stNumberInput"] button { background:var(--navy-mid) !important; color:#CBD5E1 !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] details { border-color:var(--navy-light) !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary, [data-testid="stSidebar"] [data-testid="stExpander"] p { color:rgba(255,255,255,.8) !important; }
/* round 2: sidebar help icons, toggles, number inputs; tab pill; mobile */
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg, [data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] svg { color:#94A3B8 !important; fill:none !important; stroke:#94A3B8 !important; opacity:1 !important; }
[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:not(:checked)) > span + div { background:#475569 !important; }
[data-testid="stSidebar"] [data-testid="stCheckbox"] label > span + div > div { background:#F8FAFC !important; }
[data-testid="stSidebar"] [data-testid="stNumberInput"] > div > div { border:1px solid var(--navy-light) !important; border-radius:8px; overflow:hidden; background:var(--navy-mid) !important; }
[data-testid="stSidebar"] [data-testid="stNumberInput"] [data-baseweb="input"] { border:none !important; background:var(--navy-mid) !important; }
.stTabs [data-baseweb="tab-list"], .stTabs [role="tablist"] { gap:.15rem !important; }
.stTabs [data-baseweb="tab"], .stTabs [role="tab"] { padding-left:.45rem !important; padding-right:.45rem !important; }
@media (max-width:640px) {
  .kpi .ks { white-space:normal; overflow:visible; }
  .kpi .kv { font-size:1.05rem; }
  .tie { flex-basis:100%; }
}
/* attention list and slim header */
.att-row { display:flex; gap:.7rem; align-items:flex-start; padding:.15rem 0; }
.att-ic { font-size:1.05rem; line-height:1.4rem; }
.att-t { font-weight:700; color:var(--text-primary); font-size:.93rem; }
.att-w { color:var(--text-secondary); font-size:.8rem; }
.att-amt { text-align:right; font-weight:800; font-variant-numeric:tabular-nums; color:var(--text-primary); }
.att-amt.neg { color:#B91C1C; }
.slimbar { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:.5rem;
           background:linear-gradient(135deg,#0F172A 0%,#1E293B 100%); color:#E2E8F0; border-radius:14px;
           padding:.7rem 1.1rem; margin:.4rem 0 .9rem; }
.slim-title { font-weight:800; font-size:1rem; color:#fff; }
.slim-meta { font-size:.85rem; color:#CBD5E1; display:flex; flex-wrap:wrap; gap:.35rem; }
.slim-co { color:#fff; font-weight:700; }
.slim-sep { color:#64748B; }
/* sidebar buttons and download buttons on navy */
[data-testid="stSidebar"] .stButton button, [data-testid="stSidebar"] .stDownloadButton button {
    background:var(--navy-mid) !important; color:#F1F5F9 !important; border:1px solid var(--navy-light) !important; }
[data-testid="stSidebar"] .stButton button:hover, [data-testid="stSidebar"] .stDownloadButton button:hover {
    border-color:#818CF8 !important; color:#fff !important; }
[data-testid="stSidebar"] .stButton button p, [data-testid="stSidebar"] .stDownloadButton button p { color:inherit !important; }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] { background:var(--navy-mid) !important; border-color:var(--navy-light) !important; }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] * { color:#CBD5E1 !important; }
@media (max-width:640px) { .att-amt { text-align:left; } }
</style>"""
