/* ─────────────────────────────────────────────────────────────
 * Scope_ShuaShuaLe — 网页端脚本
 * 四个页面共用一份：按页面上存在的元素决定初始化哪一块。
 * ───────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const esc = s => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };
const fmtSize = n => n < 1024 ? n + ' B' : (n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(2) + ' MB');
const fmtTime = ts => {
    if (!ts) return '—';
    const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
/* SI 前缀：0.020235573 s → 20.236 ms，0.000001154 s → 1.154 µs，49418 Hz → 49.418 kHz。
   小数位取 items 配置里的精度，最多 3 位（配置 9 位是给命令行看的） */
const SI_PREFIX = [[1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''], [1e-3, 'm'], [1e-6, 'µ'], [1e-9, 'n'], [1e-12, 'p']];
function fmtSI(v, digits, unit) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    const fixed = Math.min(Math.max(digits, 0), 3);
    if (v === 0) return '0 ' + unit;
    if (unit === '%') return Number(v).toFixed(fixed) + ' %';
    const a = Math.abs(v);
    for (const [f, pre] of SI_PREFIX) {
        if (a >= f) return (v / f).toFixed(fixed) + ' ' + pre + unit;
    }
    return v.toExponential(2) + ' ' + unit;
}

async function api(path, opts = {}) {
    /* no-store：每次刷新都读仪器当下状态，不拿浏览器缓存的旧 JSON */
    const res = await fetch(path, { cache: 'no-store',
        headers: { 'Content-Type': 'application/json' }, ...opts });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.msg || ('HTTP ' + res.status));
    return data;
}

function setConn(mode) {
    const dot = $('connDot');
    if (!dot) return;
    dot.className = 'conn-dot' + (mode === 'live' ? ' on' : mode === 'demo' ? ' demo' : '');
    $('connText').textContent = mode === 'live' ? '已连接' : mode === 'demo' ? 'DEMO 仿真' : '离线';
    if (mode === 'demo') {
        if (!document.title.includes('[DEMO]')) document.title += ' [DEMO]';
        if ($('demoBadge')) $('demoBadge').innerHTML = ' <span class="demo-badge" title="没有连接真机，数据是仿真出来的">DEMO</span>';
    }

}

/* ── 波形 ───────────────────────────────────────────────────── */
let busy = false, setup = null, stopped = false, lastWave = null;
let trigSchema = null, trigRendered = null, trigBusy = false;
let curSchema = null, curRendered = null, curBusy = false;
let trigChain = Promise.resolve();
let waveHit = null, drag = null;        // 图上命中区 / 正在拖的通道或光标
let hoverChan = null, pinnedChan = null; // 图例悬停突出 / 点击锁定
let hoverCursor = null;                  // 悬停到哪根光标
let badChans = new Set();                // 坏帧通道（AUTO 深存储）：不画，只提示
let curLocalXY = false;                  // X-Y 只切网页（固件不认），X/Y 才写仪器
let overlapNoted = false;
const DIV_H = 14, DIV_V = 8;            // DS2102E 屏幕：横 14 格 × 纵 8 格

async function initStation() {
    drawWave();                          // 先把画布按窗口算好，别等接口回来才撑开
    const cfg = await api('/api/config');
    setConn(cfg.mode);
    trigSchema = cfg.trigger || null;
    curSchema = cfg.cursor || null;
    initTriggerDD();
    initCursorDD();
    await refreshSetup();
    $('autoRefresh').onchange = () => { if ($('autoRefresh').checked) load(); };
    setInterval(refreshSetup, 5000);     // 档位不会一秒一变，每秒查纯属白占会话
    setInterval(pollRun, 2000);
    addEventListener('resize', () => drawWave());   // 窗口一变立刻按新尺寸重画
    const cv = $('wave');
    cv.addEventListener('pointerdown', onWaveDown);
    cv.addEventListener('pointermove', onWaveMove);
    cv.addEventListener('pointerup', onWaveUp);
    cv.addEventListener('pointercancel', onWaveUp);
    cv.addEventListener('pointerleave', () => {
        if (drag) return;
        cv.style.cursor = 'default';
        if (hoverChan) { hoverChan = null; drawWave(); }
    });
    const closeDds = () => {
        document.querySelectorAll('.cursor-card .cur-dd.open, .trig-card .trig-dd.open')
            .forEach(dd => {
                dd.classList.remove('open');
                const m = dd.querySelector('.dd-menu');
                if (m) m.hidden = true;
            });
    };
    document.addEventListener('click', e => {
        document.querySelectorAll('.cursor-card .cur-dd.open, .trig-card .trig-dd.open')
            .forEach(dd => {
                if (dd.contains(e.target)) return;
                dd.classList.remove('open');
                const m = dd.querySelector('.dd-menu');
                if (m) m.hidden = true;
            });
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDds(); });
    pollRun();
    load();
}

async function refreshSetup() {
    /* 拖波形 / 拖光标期间，轮询读回的是仪器旧值 —— 别把用户正拖的位置拽回去 */
    const keep = drag && drag.kind === 'chan' && setup && setup.channels[drag.ch]
        ? { ch: drag.ch, offset: setup.channels[drag.ch].offset } : null;
    const keepCur = drag && drag.kind === 'cursor' && setup && setup.cursor
        ? { id: drag.id, px: setup.cursor.pos[drag.id] } : null;
    try { setup = await api('/api/wave-setup'); } catch (e) { /* 读不到就等下一轮 */ }
    if (keep && setup && setup.channels[keep.ch]) {
        setup.channels[keep.ch].offset = keep.offset;
    }
    if (keepCur && setup && setup.cursor) {
        setup.cursor.pos[keepCur.id] = keepCur.px;
    }
    if (setup && setup.cursor && curLocalXY) setup.cursor.display = 'XY';
    updateHint();
    if (setup) renderTrigger(setup.trig);   // 触发卡跟着仪器的真实值走
    if (setup) renderCursorCard(setup.cursor,
        waveHit && waveHit.cursors ? waveHit.cursors.vals : null);
}

async function pollRun() {
    try { stopped = (await api('/api/trig')).raw === 'STOP'; } catch (e) { stopped = false; }
    updateHint();
}

/* 底下一行只管采集状态；档位进了画布图例，时基在网格上方（照仪器顶栏） */
function updateHint() {
    const el = $('statusHint');
    el.textContent = stopped ? '已冻结（STOP）' : '运行中';
    el.classList.toggle('stop', stopped);      // 冻结照仪器 STOP 标红
    $('timebaseTxt').textContent = setup && setup.timebase
        ? `${fmtDiv(setup.timebase, 's')}/div` : '';
}

async function load() {
    if (busy) return;
    busy = true;
    try {
        const d = await api('/api/snapshot?meas=0');
        setConn(d.mode);
        badChans = new Set(d.wave_bad || []);
        if (badChans.size) {
            noteWave('波形数据异常：仪器存储深度是 AUTO（5.6M），请在仪器上改成 7M', true);
        }
        drawWave(d.waveform);
    } catch (e) {
        setConn(null);
        drawWave(null);
    }
    busy = false;
    if ($('autoRefresh').checked) setTimeout(load, 1000);
}

/* 档位写法对齐仪器面板：5.00 V / 500 mV / 20.0 ms */
function fmtDiv(v, unit) {
    if (!v) return '—';
    let f = 1, pre = '';
    for (const [s, p] of SI_PREFIX) { if (Math.abs(v) >= s) { f = s; pre = p; break; } }
    const x = v / f, a = Math.abs(x);
    return x.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2) + ' ' + pre + unit;
}

/* 双通道线色：示波器的黄/青，白底上各加深一档才看得清 */
const CHAN_COLORS = { CHANnel1: '#C79A00', CHANnel2: '#009FA6' };
const TRIG_COLOR = '#E8890C';           // 仪器的触发标记是橙色

/* 通道色 → 半透明底（图例突出态的底衬） */
function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
/* 色块上的字色：亮底用黑字，深底用白字 */
function textOn(hex) {
    const n = parseInt(hex.slice(1), 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) > 140 ? '#1b1b1b' : '#fff';
}

/* ── 光标（关闭 / 手动）────────────────────────────────────── */
const CUR_COLORS = { x: '#4E6E8E', y: '#6E5C8E' };
const CUR_SRC = ['CHANnel1', 'CHANnel2'];

/* 四根线：X 的 A/B 竖线、Y 的 A/B 横线。位置存的是仪器像素（0–699 / 0–399），
   和屏幕刻度一一对应；画在网页上就是 700×400 映射到网格。
   显示模式 X / Y / X-Y 决定哪几条出现（和仪器面板一致）。 */
function drawCursors(g, cs, geo) {
    if (!cs || cs.mode !== 'MANual' || !cs.pos) return null;
    const { x0, x1, y0, y1, pw, ph, cy, dy, tL, tSpan } = geo;
    const iToX = ix => x0 + ix / 700 * pw;
    const iToY = iy => y0 + iy / 400 * ph;
    const disp = cs.display || 'X';

    /* X 用采集轴（和波形同一套 t0/dt）；Y 用光标源通道的档位。
       时间按「14 格 × 时基」算 —— 和仪器屏幕的格子约定一致，
       波形数据偶尔取不满 14 格也不会把读数带偏。 */
    const src = CUR_SRC.includes(cs.source) ? cs.source : 'CHANnel1';
    const sc = setup && setup.channels[src];
    const span = (setup && setup.timebase) ? setup.timebase * DIV_H : tSpan;
    const tOf = wx => tL + (wx - x0) / pw * span;
    const vOf = wy => (sc && sc.scale)
        ? (cy - wy) / dy * sc.scale - (sc.offset || 0) : NaN;

    const lines = [
        { id: 'ax', axis: 'x', at: iToX(cs.pos.ax) },
        { id: 'bx', axis: 'x', at: iToX(cs.pos.bx) },
        { id: 'ay', axis: 'y', at: iToY(cs.pos.ay) },
        { id: 'by', axis: 'y', at: iToY(cs.pos.by) },
    ].filter(l => disp === 'XY' || (disp === 'X' ? l.axis === 'x' : l.axis === 'y'));
    const act = drag && drag.kind === 'cursor' ? drag.id : null;

    g.save();
    g.beginPath(); g.rect(x0, y0, pw, ph); g.clip();
    for (const l of lines) {
        const hot = l.id === act || (!act && l.id === hoverCursor);
        g.strokeStyle = CUR_COLORS[l.axis];
        g.globalAlpha = act && !hot ? 0.4 : 1;
        g.lineWidth = hot ? 2.4 : 1.4;
        g.setLineDash(l.id[0] === 'a' ? [] : [5, 3]);   // A 实线、B 虚线
        g.beginPath();
        if (l.axis === 'x') { g.moveTo(l.at, y0); g.lineTo(l.at, y1); }
        else { g.moveTo(x0, l.at); g.lineTo(x1, l.at); }
        g.stroke();
    }
    g.setLineDash([]);
    g.globalAlpha = 1;
    g.restore();

    /* 标签：X 在底边（A 在线左、B 在线右），Y 在右边（A 线上、B 线下）。
       这样 A/B 完全重合时标签也不会叠在一起，各抓各的。 */
    const TW = 14, TH = 13;
    const tags = [];
    for (const l of lines) {
        const isA = l.id[0] === 'a';
        let t;
        if (l.axis === 'x') {
            t = { id: l.id, x: isA ? l.at - TW - 2 : l.at + 2,
                  y: y1 - TH - 2, w: TW, h: TH };
        } else {
            t = { id: l.id, x: x1 - TW - 2,
                  y: isA ? l.at - TH - 2 : l.at + 2, w: TW, h: TH };
        }
        t.x = Math.min(Math.max(t.x, x0 + 1), x1 - TW - 1);
        t.y = Math.min(Math.max(t.y, y0 + 1), y1 - TH - 1);
        for (const o of tags) {                 // 贴边时两个标签别叠在一起
            if (t.x < o.x + o.w + 1 && t.x + t.w + 1 > o.x
                && t.y < o.y + o.h + 1 && t.y + t.h + 1 > o.y) {
                if (l.axis === 'x') t.y = o.y - TH - 1;
                else t.x = o.x - TW - 1;
            }
        }
        tags.push(t);
    }

    g.font = 'bold 9px HarmonySans, "Microsoft YaHei", sans-serif';
    g.textAlign = 'center'; g.textBaseline = 'middle';
    for (const t of tags) {
        const hot = t.id === act || (!act && t.id === hoverCursor);
        g.globalAlpha = act && !hot ? 0.4 : 1;
        g.fillStyle = CUR_COLORS[t.id[1]];
        g.beginPath();
        if (g.roundRect) g.roundRect(t.x, t.y, t.w, t.h, 3);
        else g.rect(t.x, t.y, t.w, t.h);
        g.fill();
        if (hot) {
            g.strokeStyle = 'rgba(0,0,0,0.55)';
            g.lineWidth = 1.2;
            g.stroke();
            g.lineWidth = 1;
        }
        g.globalAlpha = 1;
        g.fillStyle = '#fff';
        g.fillText(t.id[0].toUpperCase(), t.x + t.w / 2, t.y + t.h / 2 + 0.5);
    }
    g.textBaseline = 'alphabetic';

    const tA = tOf(iToX(cs.pos.ax)), tB = tOf(iToX(cs.pos.bx));
    const vA = vOf(iToY(cs.pos.ay)), vB = vOf(iToY(cs.pos.by));
    /* 不拖的时候直接照抄仪器回读（和示波器屏幕上的数一模一样）；
       拖动中仪器还没收到新位置，先用本地估算跟手，松手后回读覆盖。 */
    const inst = cs.val || {};
    /* 不拖的时候直接照抄仪器回读（和示波器屏幕上的数一模一样）；
       拖动中仪器还没收到新位置，先用本地估算跟手，松手后回读覆盖。 */
    const live = !(drag && drag.kind === 'cursor');
    const pick = (k, local) =>
        (live && Number.isFinite(inst[k])) ? inst[k] : local;
    const dt = pick('dx', tB - tA);
    return { tags, lines, vals: {
        tA: pick('ax', tA), tB: pick('bx', tB),
        vA: pick('ay', vA), vB: pick('by', vB),
        dt, dv: pick('dy', vB - vA),
        hz: pick('ix', dt ? 1 / Math.abs(dt) : NaN),
    } };
}

function drawWave(w) {
    if (w !== undefined) lastWave = w;      // resize 重画时沿用上一帧数据
    w = lastWave;
    waveHit = null;
    const cv = $('wave');
    const dpr = window.devicePixelRatio || 1;

    /* 卡片要包住图，所以先按外层可用宽度算格子，再把画布尺寸写死。
       14:8 等比取宽、高能放下的较小格 —— 窗口一变，整块图跟着缩放。 */
    /* SIDE = 图左/右留白。左侧只放一个手柄的宽（重合时手柄内部左右分半），
       不再为两列手柄把左边距撑到三倍 */
    const PAD_T = 8, PAD_B = 22, SIDE = 26, CARD_PAD = 46;   // 46 = 卡片左右 padding 44 + 边框 2
    const row = cv.closest('.stats-row') || cv.parentElement;
    const card = cv.closest('.stat-card');
    /* 左右占位卡（以及任何兄弟卡）先把宽度占掉，剩下的才是图的 */
    const sib = [...row.children].filter(el =>
        el !== card && el.getBoundingClientRect().width > 0);   // display:none 的占位卡不占宽
    const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
    const column = getComputedStyle(row).flexDirection === 'column';
    const usedW = column ? 0                                  // 窄屏上下排：兄弟卡在下一行，不占本行宽
        : sib.reduce((s, el) => s + el.getBoundingClientRect().width, 0) + sib.length * gap;
    const availW = Math.max(200, row.clientWidth - usedW - CARD_PAD);
    const docTop = cv.getBoundingClientRect().top + window.scrollY;
    const availH = Math.max(160, window.innerHeight - docTop - 96);   // 96 = 卡片下沿 + 页脚
    const cell = Math.max(12, Math.min((availW - 2 * SIDE) / DIV_H, (availH - PAD_T - PAD_B) / DIV_V));
    const pw = cell * DIV_H, ph = cell * DIV_V;
    const W = Math.round(pw + 2 * SIDE), H = Math.round(ph + PAD_T + PAD_B);
    cv.style.width = W + 'px';
    cv.style.height = H + 'px';
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);

    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    const x0 = SIDE, x1 = W - SIDE, y0 = PAD_T, y1 = y0 + ph;
    /* X=0 的位置（触发时刻）先算出来：网格在它下面要空出来，交给 0 线自己画 */
    const ax0 = Object.values(w || {}).find(d => d && d.v && d.v.length);
    const span0 = ax0 && ax0.n && ax0.dt ? ax0.n * ax0.dt : 1;
    const xt = ax0 ? x0 + (0 - ax0.t0) / span0 * pw : x0;

    /* 网格照仪器屏：普通网格是点线，0 线的位置空出来 */
    g.lineWidth = 1;
    g.strokeStyle = 'rgba(0,0,0,0.14)';
    g.setLineDash([1, 4]);                  // 小方点
    for (let i = 1; i < DIV_H; i++) {
        const x = x0 + pw * i / DIV_H;
        if (Math.abs(x - xt) < 0.5) continue;   // X=0 交给 0 线
        g.beginPath(); g.moveTo(x, y0); g.lineTo(x, y1); g.stroke();
    }
    for (let i = 1; i < DIV_V; i++) {
        if (i === DIV_V / 2) continue;          // Y=0 交给 0 线
        const y = y0 + ph * i / DIV_V;
        g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke();
    }
    g.setLineDash([]);

    /* 四周轴线：实线，和网格同色，不抢眼 */
    g.strokeStyle = 'rgba(0,0,0,0.14)';
    g.strokeRect(x0 + 0.5, y0 + 0.5, pw - 1, ph - 1);

    /* 轴上小刻度：一格 4 小格，朝里（照仪器）。
       纵轴卡齿和通道手柄共用；横轴也给同样的细节 */
    for (let i = 0; i <= DIV_V; i++) {
        const yy = y0 + ph * i / DIV_V;
        g.strokeStyle = 'rgba(0,0,0,0.14)';
        g.beginPath(); g.moveTo(x0, yy); g.lineTo(x0 + 7, yy); g.stroke();
        g.beginPath(); g.moveTo(x1, yy); g.lineTo(x1 - 7, yy); g.stroke();
        if (i === DIV_V) break;
        g.strokeStyle = 'rgba(0,0,0,0.12)';
        for (let k = 1; k < 5; k++) {          // 一格 4 道小刻度（5 等分）
            const yk = yy + ph / DIV_V * k / 5;
            g.beginPath(); g.moveTo(x0, yk); g.lineTo(x0 + 4, yk); g.stroke();
            g.beginPath(); g.moveTo(x1, yk); g.lineTo(x1 - 4, yk); g.stroke();
        }
    }
    for (let i = 0; i <= DIV_H; i++) {
        const xx = x0 + pw * i / DIV_H;
        g.strokeStyle = 'rgba(0,0,0,0.14)';
        g.beginPath(); g.moveTo(xx, y0); g.lineTo(xx, y0 + 7); g.stroke();
        g.beginPath(); g.moveTo(xx, y1); g.lineTo(xx, y1 - 7); g.stroke();
        if (i === DIV_H) break;
        g.strokeStyle = 'rgba(0,0,0,0.12)';
        for (let k = 1; k < 5; k++) {          // 一格 4 道小刻度（5 等分）
            const xk = xx + pw / DIV_H * k / 5;
            g.beginPath(); g.moveTo(xk, y0); g.lineTo(xk, y0 + 4); g.stroke();
            g.beginPath(); g.moveTo(xk, y1); g.lineTo(xk, y1 - 4); g.stroke();
        }
    }

    g.font = '12px HarmonySans, "Microsoft YaHei", sans-serif';
    const nosignal = msg => {
        g.fillStyle = '#999';
        g.fillText(msg, x0 + 6, y0 + ph / 2);
    };
    const all = Object.keys(w || {}).map(ch => ({ ch, d: w[ch] }))
        .filter(t => t.d && t.d.v && t.d.v.length);
    if (!setup || !all.length) { nosignal(setup ? '无波形数据' : '等待档位…'); return; }

    /* 关掉的通道仪器屏幕也不显示 */
    const traces = all.filter(t => {
        const c = setup.channels[t.ch];
        return c && c.scale && c.display !== '0' && !badChans.has(t.ch);
    });
    if (!traces.length) { nosignal('无波形数据'); return; }

    /* 时间轴用第一路的 t0/dt：两路本来就取同一个采集窗口 */
    const ax = traces[0].d, tL = ax.t0, tSpan = ax.n * ax.dt;
    const cy = y0 + ph / 2, dy = ph / DIV_V;
    /* 偏移符号按实测：`:CHANnel:OFFSet` = −(中心电压)，所以中心电压 = −offset
       （cli/scopecli.py 的 fit 里记着这台机器的实测结论） */
    const yOf = (v, c) => cy - (v + (c.offset || 0)) / c.scale * dy;

    /* X/Y 0 线：照仪器屏 —— 每 1/5 格一道小横杠，两根横杠之间 4 个点。
       点用普通网格同色；横杠只比网格深一点。画在波形下面。 */
    const STEP_H = pw / (DIV_H * 5), STEP_V = ph / (DIV_V * 5);
    g.fillStyle = 'rgba(0,0,0,0.14)';
    for (let k = 0; k < DIV_H * 5; k++) {
        for (let j = 1; j <= 4; j++) {          // 两根横杠之间 4 个点
            const x = x0 + STEP_H * (k + j / 5);
            g.fillRect(x - 0.5, cy - 0.5, 1, 1);
        }
    }
    if (xt >= x0 && xt <= x1) {
        for (let k = 0; k < DIV_V * 5; k++) {
            for (let j = 1; j <= 4; j++) {
                const y = y0 + STEP_V * (k + j / 5);
                g.fillRect(xt - 0.5, y - 0.5, 1, 1);
            }
        }
    }
    g.strokeStyle = 'rgba(0,0,0,0.16)';         // 小横杠：比网格只深一点
    g.lineWidth = 1;
    for (let k = 0; k <= DIV_H * 5; k++) {
        const x = x0 + STEP_H * k;
        if (Math.abs(x - xt) < 0.5) continue;   // 交点交给小十字，别叠深
        g.beginPath(); g.moveTo(x, cy - 3.5); g.lineTo(x, cy + 3.5); g.stroke();
    }
    if (xt >= x0 && xt <= x1) {
        for (let k = 0; k <= DIV_V * 5; k++) {
            const y = y0 + STEP_V * k;
            if (Math.abs(y - cy) < 0.5) continue;
            g.beginPath(); g.moveTo(xt - 3.5, y); g.lineTo(xt + 3.5, y); g.stroke();
        }
        /* 中心小十字：用网格同色，别比周围横杠显眼 */
        g.strokeStyle = 'rgba(0,0,0,0.14)';
        g.beginPath(); g.moveTo(xt, cy - 3.5); g.lineTo(xt, cy + 3.5); g.stroke();
        g.beginPath(); g.moveTo(xt - 3.5, cy); g.lineTo(xt + 3.5, cy); g.stroke();
    }

    /* 突出通道：拖动 > 图例悬停 > 图例锁定 */
    const focus = [drag && drag.ch, hoverChan, pinnedChan]
        .find(ch => ch && traces.some(t => t.ch === ch)) || null;

    /* 两条通道是否几乎重合（按屏幕像素比，最多抽 300 点） */
    let overlap = null;
    if (traces.length >= 2) {
        const a = traces[0], b = traces[1];
        const step = Math.max(1, Math.floor(a.d.n / 300));
        let close = 0, total = 0;
        for (let i = 0; i < a.d.n; i += step) {
            const t = a.d.t0 + i * a.d.dt;
            const j = Math.round((t - b.d.t0) / b.d.dt);
            if (j < 0 || j >= b.d.n) continue;
            const ya = yOf(a.d.v[i], setup.channels[a.ch]);
            const yb = yOf(b.d.v[j], setup.channels[b.ch]);
            total++;
            if (Math.abs(ya - yb) <= 6) close++;
        }
        if (total && close / total >= 0.65) {
            overlap = { ratio: close / total, chs: [a.ch, b.ch], top: b.ch };
        }
    }

    /* 曲线按真实档位落点；出屏部分裁掉（仪器也是被屏幕边切掉）。
       突出通道最后画（置顶）并加粗；其余淡化。没突出时若两路重合，
       把上面那条改成虚线，底下那条才透得出来。 */
    g.save();
    g.beginPath(); g.rect(x0, y0, pw, ph); g.clip();
    g.lineJoin = 'round';
    const order = traces.slice().sort((a, b) =>
        (a.ch === focus ? 1 : 0) - (b.ch === focus ? 1 : 0));
    for (const t of order) {
        const c = setup.channels[t.ch], d = t.d;
        const focused = t.ch === focus;
        const dotted = !focus && overlap && t.ch === overlap.top;
        g.globalAlpha = (focus && !focused) ? 0.26 : 1;
        g.lineWidth = focused ? 2.6 : (dotted ? 2.8 : 1.6);
        g.lineCap = dotted ? 'round' : 'butt';
        /* [0.1, 6] + 圆头 = 一个个圆点，和示波器的点线同一种语言 */
        g.setLineDash(dotted ? [0.1, 6] : []);
        g.strokeStyle = CHAN_COLORS[t.ch] || '#0B3868';
        g.beginPath();
        for (let i = 0; i < d.n; i++) {
            const x = x0 + (d.t0 + i * d.dt - tL) / tSpan * pw;
            const y = yOf(d.v[i], c);
            i ? g.lineTo(x, y) : g.moveTo(x, y);
        }
        g.stroke();
    }
    g.setLineDash([]);
    g.globalAlpha = 1;
    g.restore();

    /* 触发位置（t = 0）顶边的橙色下三角；竖线已经和 X=0 线一起画在波形下面 */
    if (xt >= x0 && xt <= x1) {
        g.fillStyle = TRIG_COLOR;
        g.beginPath(); g.moveTo(xt - 5, y0); g.lineTo(xt + 5, y0); g.lineTo(xt, y0 + 9);
        g.closePath(); g.fill();
    }

    /* 触发电平：右边缘橙色小三角。
       边沿/脉宽一个电平；斜率/欠幅是 A/B 双电平窗口 */
    const tr = setup.trig || {};
    const levels = [];
    if (tr.type === 'EDGE' || tr.type === 'PULSE') {
        if (Number.isFinite(tr.level)) levels.push(tr.level);
    } else if (tr.type === 'SLOPE' || tr.type === 'RUNT') {
        if (Number.isFinite(tr.a)) levels.push(tr.a);
        if (Number.isFinite(tr.b)) levels.push(tr.b);
    }
    const srcNo = String(tr.source || '').replace(/\D/g, '');
    const sc = srcNo ? setup.channels['CHANnel' + srcNo] : null;
    if (sc && sc.scale) {
        /* 和左边通道 0 位手柄对称：右留白里一个 T 色块 + 朝左的箭头，
           箭头尖落在触发电平上。边沿/脉宽一个；斜率/欠幅 A/B 两个。 */
        const BW = 20, BH = 14, TAIL = 4;
        const bx = W - BW - 1;
        g.font = 'bold 10px HarmonySans, "Microsoft YaHei", sans-serif';
        g.textAlign = 'center'; g.textBaseline = 'middle';
        for (const lv of levels) {
            const y = yOf(lv, sc);
            if (y < y0 || y > y1) continue;
            const by = Math.min(Math.max(y - BH / 2, y0), y1 - BH);
            g.fillStyle = TRIG_COLOR;
            g.beginPath();
            g.moveTo(bx, y - 5);
            g.lineTo(bx - TAIL, y);
            g.lineTo(bx, y + 5);
            g.closePath(); g.fill();
            g.beginPath();
            if (g.roundRect) g.roundRect(bx, by, BW, BH, 3);
            else g.rect(bx, by, BW, BH);
            g.fill();
            g.fillStyle = '#fff';
            g.fillText('T', bx + BW / 2, by + BH / 2 + 0.5);
        }
    }

    /* 左边缘：通道零位手柄。一个手柄的宽就够，不再为两列撑宽左边距。
       两路零位重合时合成一个「2 | 1」双色手柄 + 一个箭头：
       左半 CH2、右半 CH1（按你指定的顺序），箭头尖仍落在精确电平上。 */
    const BW = 20, HALF = BW / 2, BH = 14, TAIL = 4;
    const marks = traces.map(t => {
        const c = setup.channels[t.ch];
        return { ch: t.ch, y: yOf(0, c), color: CHAN_COLORS[t.ch] || '#0B3868' };
    });
    const split = marks.length === 2 && Math.abs(marks[0].y - marks[1].y) <= 6;
    g.font = 'bold 9px HarmonySans, "Microsoft YaHei", sans-serif';
    g.textAlign = 'center'; g.textBaseline = 'middle';
    const markerBoxes = [];
    const drawTail = (bx, y, c1, c2) => {        // 一个箭头；双色手柄时左→右渐变
        const grad = g.createLinearGradient(bx + BW, 0, bx + BW + TAIL, 0);
        grad.addColorStop(0, c1);
        grad.addColorStop(1, c2 || c1);
        g.fillStyle = grad;
        g.beginPath();
        g.moveTo(bx + BW, y - 5);
        g.lineTo(bx + BW + TAIL, y);
        g.lineTo(bx + BW, y + 5);
        g.closePath();
        g.fill();
    };
    const ring = (x, y, w, h) => {
        g.strokeStyle = 'rgba(0,0,0,0.55)';
        g.lineWidth = 1.5;
        g.beginPath();
        if (g.roundRect) g.roundRect(x, y, w, h, 3);
        else g.rect(x, y, w, h);
        g.stroke();
        g.lineWidth = 1;
    };

    if (split) {
        /* 左 CH2、右 CH1 是用户指定的顺序；不依赖 traces 的遍历顺序 */
        const left = marks.find(m => m.ch === 'CHANnel2') || marks[1];
        const right = marks.find(m => m.ch === 'CHANnel1') || marks[0];
        const bx = 1;
        const y = Math.min(Math.max((left.y + right.y) / 2, y0 + BH / 2), y1 - BH / 2);
        g.save();
        g.beginPath();
        if (g.roundRect) g.roundRect(bx, y - BH / 2, BW, BH, 3);
        else g.rect(bx, y - BH / 2, BW, BH);
        g.clip();
        g.fillStyle = left.color; g.fillRect(bx, y - BH / 2, HALF, BH);
        g.fillStyle = right.color; g.fillRect(bx + HALF, y - BH / 2, HALF, BH);
        g.restore();
        g.fillStyle = 'rgba(255,255,255,0.9)';
        g.fillRect(bx + HALF - 0.5, y - BH / 2 + 1, 1, BH - 2);   // 两半之间的分隔线
        g.fillStyle = textOn(left.color);
        g.fillText(left.ch.replace('CHANnel', ''), bx + HALF / 2, y + 0.5);
        g.fillStyle = textOn(right.color);
        g.fillText(right.ch.replace('CHANnel', ''), bx + HALF * 1.5, y + 0.5);
        drawTail(bx, y, left.color, right.color);
        markerBoxes.push({ ch: left.ch, x: bx, y: y - BH / 2, w: HALF, h: BH });
        markerBoxes.push({ ch: right.ch, x: bx + HALF, y: y - BH / 2,
                           w: HALF + TAIL, h: BH });
        if (focus === left.ch || focus === right.ch) {
            const fx = focus === left.ch ? bx : bx + HALF;
            ring(fx - 0.5, y - BH / 2 - 0.5, HALF + 1, BH + 1);
        }
    } else {
        const bx = 1;
        for (const m of marks) {
            const y = Math.min(Math.max(m.y, y0 + BH / 2), y1 - BH / 2);
            markerBoxes.push({ ch: m.ch, x: bx, y: y - BH / 2, w: BW + TAIL, h: BH });
            g.fillStyle = m.color;
            g.beginPath();
            if (g.roundRect) g.roundRect(bx, y - BH / 2, BW, BH, 3);
            else g.rect(bx, y - BH / 2, BW, BH);
            g.fill();
            drawTail(bx, y, m.color);
            g.fillStyle = textOn(m.color);
            g.fillText(m.ch.replace('CHANnel', ''), bx + BW / 2, y + 0.5);
            if (m.ch === focus) ring(bx - 0.5, y - BH / 2 - 0.5, BW + TAIL + 1, BH + 1);
        }
    }

    /* 0 V 吸附：中心线打一条点线，明确告诉用户吸住了 */
    if (drag && drag.snapped) {
        g.strokeStyle = 'rgba(232,137,12,0.55)';
        g.lineWidth = 1;
        g.setLineDash([1, 3]);
        g.beginPath(); g.moveTo(x0, cy); g.lineTo(x1, cy); g.stroke();
        g.setLineDash([]);
    }

    /* 网格下方：档位图例（悬停突出 / 点击锁定），照仪器左下角的 `1 — 100mV` */
    g.font = '12px HarmonySans, "Microsoft YaHei", sans-serif';
    g.textAlign = 'start'; g.textBaseline = 'middle';
    let lx = x0;
    const legend = [];
    for (const t of traces) {
        const c = setup.channels[t.ch];
        const txt = `${t.ch.replace('CHANnel', 'CH')} ${fmtDiv(c.scale, 'V')}`;
        const itemW = 15 + g.measureText(txt).width;
        const focused = t.ch === focus;
        if (focused) {                       // 突出态垫一层淡色底，图例本身也是操作区
            g.fillStyle = hexA(CHAN_COLORS[t.ch] || '#0B3868', 0.10);
            g.beginPath();
            if (g.roundRect) g.roundRect(lx - 4, y1 + 4, itemW + 8, 18, 6);
            else g.rect(lx - 4, y1 + 4, itemW + 8, 18);
            g.fill();
        }
        g.globalAlpha = (focus && !focused) ? 0.45 : 1;
        g.fillStyle = CHAN_COLORS[t.ch] || '#0B3868';
        g.fillRect(lx, y1 + 8, 10, 10);
        g.font = (focused ? 'bold ' : '') + '12px HarmonySans, "Microsoft YaHei", sans-serif';
        g.fillText(txt, lx + 15, y1 + 13);
        g.font = '12px HarmonySans, "Microsoft YaHei", sans-serif';
        g.globalAlpha = 1;
        legend.push({ ch: t.ch, x: lx - 4, y: y1 + 4, w: itemW + 8, h: 18 });
        lx += itemW + 18;
    }

    /* 光标画在最上层：波形、手柄、图例都盖不住它 */
    const curHit = drawCursors(g, setup.cursor,
        { x0, x1, y0, y1, pw, ph, cy, dy, tL, tSpan });
    g.textBaseline = 'alphabetic';

    /* 图上命中区：左边缘色块、波形线、底部图例、四根光标 */
    waveHit = {
        x0, y0, x1, y1, pw, ph, tL, tSpan, cy, dy,
        markers: markerBoxes, legend, overlap, cursors: curHit,
        traces: traces.map(t => ({ ch: t.ch, d: t.d, c: setup.channels[t.ch] })),
    };
    renderCursorCard(setup.cursor, curHit ? curHit.vals : null);

    /* 重叠第一次出现时提示一次，不覆盖拖动/触发留下的结果 */
    const wn = $('waveNote');
    if (wn) {
        if (overlap && !overlapNoted && !wn.textContent) {
            overlapNoted = true;
            noteWave('波形重叠：移入下方图例可单独突出，点击锁定');
        } else if (!overlap && overlapNoted) {
            overlapNoted = false;
            if ((wn.textContent || '').startsWith('波形重叠')) noteWave('');
        }
    }
}

/* ── 图上拖动通道垂直位置 ───────────────────────────────────── */
/* 抓左边 1/2 色块或波形线本体，上下拖 → 改 :CHANnel<n>:OFFSet。
   仪器行为：offset 增大，波形上移（见 scopecli.py fit 的实测注释）。 */
function hitWaveChannel(px, py, prefer) {
    const h = waveHit;
    if (!h) return null;
    for (const m of h.markers) {                // 左边缘色块是明确句柄，优先
        if (px >= m.x - 4 && px <= m.x + m.w + 4
            && py >= m.y - 4 && py <= m.y + m.h + 4) return m.ch;
    }
    if (px < h.x0 || px > h.x1) return null;
    let best = null, bestD = 9;                 // 9 px 内算抓到线
    const near = [];
    for (const t of h.traces) {
        if (!t.c || !t.c.scale) continue;
        const tv = h.tL + (px - h.x0) / h.pw * h.tSpan;
        const i = Math.round((tv - t.d.t0) / t.d.dt);
        if (i < 0 || i >= t.d.n) continue;
        const y = h.cy - (t.d.v[i] + (t.c.offset || 0)) / t.c.scale * h.dy;
        const d = Math.abs(y - py);
        if (d < 10) near.push(t.ch);
        if (d < bestD) { bestD = d; best = t.ch; }
    }
    /* 两路重合时按锁定通道命中 —— 否则永远只能抓到先画的那一路 */
    return (prefer && near.includes(prefer)) ? prefer : best;
}

function hitLegend(px, py) {
    const h = waveHit;
    if (!h || !h.legend) return null;
    for (const it of h.legend) {
        if (px >= it.x && px <= it.x + it.w && py >= it.y && py <= it.y + it.h) return it.ch;
    }
    return null;
}

function onWaveDown(ev) {
    if (!setup || ev.button !== 0) return;
    const cv = $('wave');
    const rect = cv.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    const cid = hitCursor(px, py);           // 光标最上层，先抓它
    if (cid) { startCursorDrag(cid, ev); return; }
    const lg = hitLegend(px, py);
    if (lg) {                                   // 点图例：锁定/解锁突出
        pinnedChan = pinnedChan === lg ? null : lg;
        hoverChan = null;
        drawWave();
        ev.preventDefault();
        return;
    }
    const ch = hitWaveChannel(px, py, pinnedChan);
    if (!ch) return;
    const c = setup.channels[ch];
    if (!c || !c.scale) return;
    hoverCursor = null;                         // 拖通道时别再留着光标高亮
    drag = { kind: 'chan', ch, startY: ev.clientY, startOffset: c.offset || 0,
             scale: c.scale, dy: waveHit.dy, moved: false, snapped: false,
             timer: null };
    cv.setPointerCapture(ev.pointerId);
    cv.style.cursor = 'ns-resize';
    noteWave('');
    ev.preventDefault();
}

/* 光标命中：标签优先，其次线身 ±4px；两条线贴在一起时取最近的，
   悬停过的那根优先 —— 标签永远是明确入口，重合也抓得到。 */
function hitCursor(px, py) {
    const h = waveHit && waveHit.cursors;
    if (!h) return null;
    for (const t of h.tags) {
        if (px >= t.x && px <= t.x + t.w && py >= t.y && py <= t.y + t.h) return t.id;
    }
    let best = null, bestD = 5;
    for (const l of h.lines) {
        const d = l.axis === 'x' ? Math.abs(px - l.at) : Math.abs(py - l.at);
        if (d < bestD) { bestD = d; best = l.id; }
        else if (Math.abs(d - bestD) < 0.5 && hoverCursor === l.id) best = l.id;
    }
    return best;
}

function startCursorDrag(id, ev) {
    const cv = $('wave');
    drag = { kind: 'cursor', id, axis: id[1] === 'x' ? 'x' : 'y',
             cursor: id[0].toUpperCase(), moved: false, timer: null };
    hoverCursor = id;
    hoverChan = null;
    cv.setPointerCapture(ev.pointerId);
    cv.style.cursor = drag.axis === 'x' ? 'ew-resize' : 'ns-resize';
    noteWave('');
    drawWave();
    ev.preventDefault();
}

function onWaveMove(ev) {
    const cv = $('wave');
    const rect = cv.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    if (!drag) {
        const cid = hitCursor(px, py);
        const lg = cid ? null : hitLegend(px, py);
        const tr = (cid || lg) ? null : hitWaveChannel(px, py, pinnedChan);
        const next = cid ? null : (lg || tr);   // 图例或波形线上都算悬停突出
        if (cid !== hoverCursor || next !== hoverChan) {
            hoverCursor = cid;
            hoverChan = next;
            drawWave();
        }
        cv.style.cursor = cid ? (cid[1] === 'x' ? 'ew-resize' : 'ns-resize')
            : lg ? 'pointer' : (tr ? 'ns-resize' : 'default');
        return;
    }
    if (drag.kind === 'cursor') { onCursorMove(px, py, ev); return; }
    const c = setup && setup.channels[drag.ch];
    if (!c || !drag.dy) return;
    const raw = drag.startOffset - (ev.clientY - drag.startY) * drag.scale / drag.dy;
    /* 硬卡齿：纵轴一小格 = 1/5 大格（一格 4 道小刻度），手柄永远落在刻度上。
       1/5 格在当前高度约 9–11px；磁吸会每隔这么点就粘一下，不如硬卡齿干脆。 */
    const step = drag.scale / 5;
    const detent = Math.round(raw / step);
    const off = detent * step;
    const snapped = detent === 0;
    drag.snapped = snapped;
    c.offset = off;
    drag.moved = true;
    drawWave();                                 // 本地先跟手，别等网络
    const wn = $('waveNote');                   // 拖动读数：两位小数
    if (wn) {
        wn.textContent = 'CH' + drag.ch.replace('CHANnel', '') + '  '
            + off.toFixed(2) + ' V' + (snapped ? '  ·  0 V 吸附' : '');
        wn.className = 'hint';
    }
    /* 拖动中只本地跟手；停住 350ms 或松手才下发一次。
       连续写会把仪器队列打满，松手后还要追几秒。 */
    clearTimeout(drag.timer);
    drag.timer = setTimeout(() => {
        const cc = drag && setup && setup.channels[drag.ch];
        if (cc) sendChannelOffset(drag.ch, cc.offset);
    }, 350);
    ev.preventDefault();
}

/* 光标拖动：屏幕像素 → 仪器像素（700×400），再卡 1/5 格（10 px）。
   10 px 正好等于画布上 0 线小横杠的间距，换档 / 换时基都不跑位。 */
function onCursorMove(px, py, ev) {
    const cs = setup && setup.cursor;
    if (!cs || !waveHit) return;
    const isX = drag.axis === 'x';
    const raw = isX ? (px - waveHit.x0) / waveHit.pw * 700
        : (py - waveHit.y0) / waveHit.ph * 400;
    const max = isX ? 690 : 390;
    const snapped = Math.max(0, Math.min(max, Math.round(raw / 10) * 10));
    if (cs.pos[drag.id] !== snapped) {
        cs.pos[drag.id] = snapped;
        drag.moved = true;
        drawWave();                             // 本地先跟手，再等停手
        clearTimeout(drag.timer);
        drag.timer = setTimeout(() => {
            const cc = setup && setup.cursor;
            if (cc) sendCursorPos(drag.id, cc.pos[drag.id]);
        }, 350);
    }
    ev.preventDefault();
}

async function onWaveUp(ev) {
    const cv = $('wave');
    if (!drag) return;
    const d = drag;
    drag = null;
    clearTimeout(d.timer);
    cv.style.cursor = 'default';
    try { cv.releasePointerCapture(ev.pointerId); } catch (e) {}
    if (d.kind === 'cursor') {
        drawWave();
        const cs = setup && setup.cursor;
        if (d.moved && cs) await sendCursorPos(d.id, cs.pos[d.id]);
        return;
    }
    if (!d.moved) {                             // 单击波形 = 锁定/解锁该通道
        pinnedChan = pinnedChan === d.ch ? null : d.ch;
        hoverChan = null;
        drawWave();
        return;
    }
    if (!pinnedChan) hoverChan = null;
    drawWave();
    const c = setup && setup.channels[d.ch];
    const off = c ? c.offset : d.startOffset;
    const ok = await sendChannelOffset(d.ch, off);
    if (ok) {
        noteWave(`${d.ch.replace('CHANnel', 'CH')} 偏移 `
            + `${fmtVolt(d.startOffset)} → ${fmtVolt(off)} V`);
    }
    await refreshSetup();                       // 以仪器为准核对一次
}

async function sendChannelOffset(chan, off) {
    try {
        const r = await api('/api/channel', {
            method: 'POST', body: JSON.stringify({ chan, offset: off }) });
        if (!r.ok) throw new Error(r.msg || '设置失败');
        return true;
    } catch (e) {
        noteWave('偏移没写进去：' + e.message, true);
        await refreshSetup();
        return false;
    }
}

async function sendCursorPos(id, px) {
    try {
        const r = await api('/api/cursor', {
            method: 'POST',
            body: JSON.stringify({ axis: id[1], cursor: id[0].toUpperCase(), px }) });
        if (!r.ok) throw new Error(r.msg || '设置失败');
        if (r.cursor_state && setup) {          // 回读覆盖本地估算，和仪器对齐
            setup.cursor = r.cursor_state;
            if (curLocalXY) setup.cursor.display = 'XY';
            drawWave();
        }
        return true;
    } catch (e) {
        noteCur('光标没写进去：' + e.message, true);
        await refreshSetup();
        return false;
    }
}

function noteWave(msg, bad) {
    const el = $('waveNote');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'hint' + (bad ? ' bad' : '');
    if (msg && !bad) {
        setTimeout(() => { if (el.textContent === msg) noteWave(''); }, 2500);
    }
}

/* ── 触发卡 ─────────────────────────────────────────────────── */
/* 类型 = 首页「排序」同款下拉；下面按类型渲染 chip 行和数值行。
   离散项点了就发，数值项回车/失焦才发 —— 不把半截数字发给仪器。 */
const DD_ARROW = '<svg class="dd-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg>';
const NUM_PREFIX = { p: 1e-12, n: 1e-9, u: 1e-6, 'µ': 1e-6, 'μ': 1e-6,
                     m: 1e-3, '': 1, k: 1e3, K: 1e3, M: 1e6, G: 1e9 };

function fmtNum(v, digits = 2) {
    if (v === null || v === undefined || !Number.isFinite(v)) return '';
    if (v === 0) return (0).toFixed(digits);
    for (const [f, pre] of SI_PREFIX) {
        if (Math.abs(v) >= f) return (v / f).toFixed(digits) + pre;
    }
    return v.toExponential(3);
}

/* 电压读数统一用 V + 两位小数（不换 mV，光标/拖动读数都按这个约定） */
function fmtVolt(v) {
    return Number.isFinite(v) ? v.toFixed(2) : '—';
}

/* 时间 / 频率读数：SI 前缀 + 两位小数（3.60 ms / 277.78 Hz） */
function fmtDec(v, unit) {
    if (!Number.isFinite(v)) return '—';
    if (v === 0) return '0.00 ' + unit;
    for (const [f, pre] of SI_PREFIX) {
        if (Math.abs(v) >= f) return (v / f).toFixed(2) + ' ' + pre + unit;
    }
    return v.toExponential(2) + ' ' + unit;
}

/* 光标读数照仪器屏幕：4 位有效数字（-2.980 ms / 10.00 V / 171.8 Hz） */
function fmtSig(v, unit) {
    if (!Number.isFinite(v)) return '—';
    if (v === 0) return '0.000 ' + unit;
    let f = 1, pre = '';
    for (const [s, p] of SI_PREFIX) {
        if (Math.abs(v) >= s) { f = s; pre = p; break; }
    }
    const x = v / f;
    const dec = Math.max(0, 3 - Math.floor(Math.log10(Math.abs(x))));
    return x.toFixed(dec) + ' ' + pre + unit;
}

/* 触发参数输入框：电压两位小数；时间仍走 SI 前缀（1.00µ / 2.00m） */
function fmtField(v, unit) {
    if (v === null || v === undefined || !Number.isFinite(v)) return '';
    return unit === 'V' ? fmtVolt(v) : fmtNum(v);
}

function parseNum(s) {
    s = String(s ?? '').trim().replace(/μ/g, 'µ').replace(/\s+/g, '');
    const m = s.match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([pnuµmkKMG]?)(?:V|s)?$/);
    if (!m) return null;
    const v = parseFloat(m[1]) * (NUM_PREFIX[m[2]] ?? 1);
    return Number.isFinite(v) ? v : null;
}

function initTriggerDD() {
    const dd = $('trigTypeDD'), menu = $('trigTypeMenu'), btn = $('trigTypeBtn');
    if (!dd || !menu || !btn || !trigSchema) return;
    menu.innerHTML = (trigSchema.types || []).map(t =>
        `<button type="button" class="dd-item" role="option" data-type="${esc(t.value)}">${esc(t.label)}</button>`
    ).join('');

    btn.onclick = e => {
        e.stopPropagation();
        const open = menu.hidden;
        menu.hidden = !open;
        dd.classList.toggle('open', open);
    };
    menu.querySelectorAll('.dd-item').forEach(b => {
        b.onclick = e => {
            e.stopPropagation();
            menu.hidden = true;
            dd.classList.remove('open');
            const cur = setup && setup.trig;
            if (!cur || cur.type === b.dataset.type) return;
            applyTrig({ type: b.dataset.type });
        };
    });
    document.addEventListener('click', e => {
        if (menu.hidden || e.target.closest('#trigTypeDD')) return;
        menu.hidden = true;
        dd.classList.remove('open');
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !menu.hidden) {
            menu.hidden = true;
            dd.classList.remove('open');
        }
    });
}

function fieldDef(type, key) {
    const fields = (trigSchema && trigSchema.fields && trigSchema.fields[type]) || [];
    return fields.find(f => f.key === key) || null;
}

/* 类型变了才重建行；同类型只更新选中/数值，避免轮询把焦点抢走 */
function renderTrigger(tr) {
    if (!tr || !tr.type) return;
    const meta = ((trigSchema && trigSchema.types) || []).find(t => t.value === tr.type);
    const txt = $('trigTypeTxt');
    if (txt) txt.textContent = meta ? meta.label : tr.type + '（本页未开放）';
    document.querySelectorAll('#trigTypeMenu .dd-item').forEach(b =>
        b.classList.toggle('active', b.dataset.type === tr.type));

    if (tr.type !== trigRendered) {
        buildTrigRows(tr);
        trigRendered = tr.type;
    }
    syncTrigRows(tr);
}

function buildTrigRows(tr) {
    const box = $('trigRows');
    if (!box) return;
    const fields = (trigSchema && trigSchema.fields && trigSchema.fields[tr.type]) || [];
    if (!tr.supported || !fields.length) {
        box.innerHTML = `<div class="hint" style="line-height:1.7">仪器当前是 <b>${esc(tr.type || '未知')}</b>，`
            + '本页只开放四种波形触发（边沿 / 脉宽 / 欠幅 / 斜率）；从上面的下拉选一种即可切回来。</div>';
        return;
    }

    box.innerHTML = fields.map(f => {
        const lab = `<span class="filter-label">${esc(f.label)}</span>`;
        if (f.kind === 'chips') {
            const opts = (f.options || []).map(o =>
                `<button type="button" class="dd-item" role="option" data-field="${esc(f.key)}" data-value="${esc(o.value)}">${esc(o.label)}</button>`
            ).join('');
            return `<div class="filter-row" data-row="${esc(f.key)}">${lab}`
                + `<div class="dd trig-dd" data-field="${esc(f.key)}">`
                + `<button type="button" class="dd-trigger" aria-haspopup="listbox">`
                + `<span class="dd-txt"></span>${DD_ARROW}</button>`
                + `<div class="dd-menu" hidden role="listbox">${opts}</div></div></div>`;
        }
        return `<div class="filter-row" data-row="${esc(f.key)}">${lab}`
            + '<label class="trig-num"><input class="inp" type="text" inputmode="decimal" '
            + `data-field="${esc(f.key)}" placeholder="0" autocomplete="off">`
            + `<span class="unit">${esc(f.unit || '')}</span></label></div>`;
    }).join('');

    box.querySelectorAll('.trig-dd').forEach(dd => {
        const btn = dd.querySelector('.dd-trigger'), menu = dd.querySelector('.dd-menu');
        btn.onclick = e => {
            e.stopPropagation();
            const open = menu.hidden;
            menu.hidden = !open;
            dd.classList.toggle('open', open);
        };
        menu.querySelectorAll('.dd-item').forEach(b => {
            b.onclick = e => {
                e.stopPropagation();
                menu.hidden = true;
                dd.classList.remove('open');
                const cur = setup && setup.trig;
                if (!cur || trigBusy || cur[b.dataset.field] === b.dataset.value) return;
                applyTrig({ type: cur.type, field: b.dataset.field, value: b.dataset.value });
            };
        });
    });
    box.querySelectorAll('input[data-field]').forEach(inp => {
        inp.onkeydown = e => {
            if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
            else if (e.key === 'Escape') { syncTrigRows(setup && setup.trig); inp.blur(); }
        };
        inp.onblur = () => {
            const cur = setup && setup.trig;
            if (!cur || trigBusy) return;
            if (inp.value === inp.dataset.shown) return;   // 只是聚焦又离开，别把显示精度当改动
            const field = inp.dataset.field;
            const v = parseNum(inp.value);
            if (v === null) {
                syncTrigRows(cur);
                noteTrig('数值看不清：' + inp.value, true);
                return;
            }
            const old = cur[field];
            if (Number.isFinite(old) && Math.abs(v - old) <= Math.abs(old || 1) * 1e-9) {
                inp.value = fmtNum(old);
                return;
            }
            applyTrig({ type: cur.type, field, value: v });
        };
    });
}

function syncTrigRows(tr) {
    if (!tr || !tr.type) return;
    const box = $('trigRows');
    if (!box) return;
    box.querySelectorAll('.filter-row[data-row]').forEach(row => {
        const key = row.dataset.row;
        const f = fieldDef(tr.type, key);
        if (!f) return;
        if (f.when) {                       // 条件变化时显隐（如脉宽只显示当前用的那一条）
            const show = Object.entries(f.when).every(([k, v]) =>
                Array.isArray(v) ? v.includes(tr[k]) : tr[k] === v);
            row.style.display = show ? '' : 'none';
            if (!show) return;
        }
        const lab = row.querySelector('.filter-label');
        if (lab) lab.textContent = (f.labelIn && tr.cond === 'IN') ? f.labelIn : f.label;
        const dd = row.querySelector('.trig-dd');
        if (dd) {
            const opt = (f.options || []).find(o => o.value === tr[key]);
            const txt = dd.querySelector('.dd-txt');
            if (txt) txt.textContent = opt ? opt.label : (tr[key] || '—');
            dd.querySelectorAll('.dd-item').forEach(b =>
                b.classList.toggle('active', b.dataset.value === tr[key]));
        }
        const inp = row.querySelector('input[data-field]');
        if (inp && document.activeElement !== inp) {
            inp.value = fmtField(tr[key], f.unit);
            inp.dataset.shown = inp.value;      // 记住显示值，聚焦又离开不算改动
        }
    });
}

/* 串行化：连续点几个 chip 也按顺序下发，不会互相覆盖 */
function applyTrig(patch) {
    trigChain = trigChain.then(() => doApplyTrig(patch), () => doApplyTrig(patch));
    return trigChain;
}

async function doApplyTrig(patch) {
    trigBusy = true;
    const card = document.querySelector('.trig-card');
    if (card) card.classList.add('busy');
    noteTrig('下发中…');
    try {
        const r = await api('/api/trigger', { method: 'POST', body: JSON.stringify(patch) });
        if (!r.ok) throw new Error(r.msg || '设置失败');
        if (setup) setup.trig = r.config;
        renderTrigger(r.config);
        noteTrig('已应用');
        drawWave();                         // 触发电平标记跟着动
    } catch (e) {
        noteTrig('未生效：' + e.message, true);
        await refreshSetup();               // 以仪器为准回读
    } finally {
        trigBusy = false;
        if (card) card.classList.remove('busy');
    }
}

function noteTrig(msg, bad) {
    const el = $('trigNote');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'trig-note hint' + (bad ? ' bad' : msg ? ' ok' : '');
    if (msg === '已应用') {
        setTimeout(() => { if (el.textContent === '已应用') noteTrig(''); }, 1500);
    }
}

/* ── 光标卡 ─────────────────────────────────────────────────── */
/* 只开放关闭 / 手动。四根线都在图上直接拖，卡片只显示读数。 */
function initCursorDD() {
    const dd = $('curModeDD'), menu = $('curModeMenu'), btn = $('curModeBtn');
    if (!dd || !menu || !btn || !curSchema) return;
    menu.innerHTML = (curSchema.modes || []).map(m =>
        `<button type="button" class="dd-item" role="option" data-mode="${esc(m.value)}">${esc(m.label)}</button>`
    ).join('');
    const close = () => { menu.hidden = true; dd.classList.remove('open'); };
    btn.onclick = e => {
        e.stopPropagation();
        const open = menu.hidden;
        menu.hidden = !open;
        dd.classList.toggle('open', open);
    };
    menu.querySelectorAll('.dd-item').forEach(b => {
        b.onclick = e => {
            e.stopPropagation();
            close();
            const cur = setup && setup.cursor;
            if (!cur || cur.mode === b.dataset.mode) return;
            applyCursorMode(b.dataset.mode);
        };
    });
    document.addEventListener('click', e => {
        if (menu.hidden || e.target.closest('#curModeDD')) return;
        close();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !menu.hidden) close();
    });
}

async function applyCursorMode(mode) {
    if (curBusy) return;
    curBusy = true;
    noteCur('切换中…');
    try {
        const r = await api('/api/cursor', { method: 'POST', body: JSON.stringify({ mode }) });
        if (!r.ok) throw new Error(r.msg || '切换失败');
        if (setup && r.cursor_state) setup.cursor = r.cursor_state;
        if (setup && setup.cursor && curLocalXY) setup.cursor.display = 'XY';
        curRendered = null;                     // 强制重建读数行
        renderCursorCard(setup && setup.cursor, null);
        drawWave();
        noteCur(mode === 'MANual' ? '图上直接拖这四根线' : '');
    } catch (e) {
        noteCur('没切成：' + e.message, true);
        await refreshSetup();
    } finally {
        curBusy = false;
    }
}

/* 显示模式 / 信源：都写仪器，改完以回读为准 */
async function applyCursor(patch) {
    if (curBusy) return;
    curBusy = true;
    try {
        const r = await api('/api/cursor', { method: 'POST', body: JSON.stringify(patch) });
        if (!r.ok) throw new Error(r.msg || '设置失败');
        if (setup && r.cursor_state) setup.cursor = r.cursor_state;
        if (setup && setup.cursor && curLocalXY) setup.cursor.display = 'XY';
        curRendered = null;
        renderCursorCard(setup && setup.cursor, null);
        drawWave();
    } catch (e) {
        noteCur('没切成：' + e.message, true);
        await refreshSetup();
    } finally {
        curBusy = false;
    }
}

function renderCursorCard(cs, vals) {
    const txt = $('curModeTxt'), box = $('curRows');
    if (!txt || !box || !cs) return;
    const meta = ((curSchema && curSchema.modes) || []).find(m => m.value === cs.mode);
    txt.textContent = meta ? meta.label : (cs.label || cs.mode);
    document.querySelectorAll('#curModeMenu .dd-item').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === cs.mode));

    const disp = cs.display || 'X';
    const showX = disp !== 'Y', showY = disp !== 'X';
    const key = [cs.mode, cs.supported ? 1 : 0, disp, cs.source || ''].join(':');
    if (key !== curRendered) {
        curRendered = key;
        if (cs.mode !== 'MANual') {
            box.innerHTML = `<div class="hint">${cs.supported ? '光标关闭'
                : '仪器当前：' + esc(cs.label || cs.mode) + '<br>本页只能切到关闭 / 手动'}</div>`;
            return;
        }
        const curDD = (label, attr, items, curVal) => {
            const opts = items.map(it =>
                `<button type="button" class="dd-item" role="option" data-${attr}="${esc(it.value)}">${esc(it.label)}</button>`).join('');
            const hit = items.find(it => it.value === curVal);
            return `<div class="filter-row cur-ctl"><span class="filter-label">${label}</span>`
                + `<div class="dd cur-dd" data-dd="${attr}">`
                + `<button type="button" class="dd-trigger" aria-haspopup="listbox">`
                + `<span class="dd-txt">${esc(hit ? hit.label : (curVal || '—'))}</span>${DD_ARROW}</button>`
                + `<div class="dd-menu" hidden role="listbox">${opts}</div></div></div>`;
        };
        const ctrl = curDD('显示', 'curdisp', (curSchema && curSchema.displays) || [], disp)
            + curDD('源', 'cursrc', (curSchema && curSchema.sources) || [], cs.source);
        const row = (lab, key) =>
            `<div class="cur-read"><span>${lab}</span><b data-v="${key}">—</b></div>`;
        let html = ctrl;
        if (showX) html += row('A-&gt;X', 'ax') + row('B-&gt;X', 'bx');
        if (showY) html += row('A-&gt;Y', 'ay') + row('B-&gt;Y', 'by');
        if (showX) html += row('ΔX', 'dx') + row('1/ΔX', 'ix');
        if (showY) html += row('ΔY', 'dy');
        box.innerHTML = html;
        box.querySelectorAll('.cur-dd').forEach(dd => {
            const btn = dd.querySelector('.dd-trigger'), menu = dd.querySelector('.dd-menu');
            btn.onclick = e => {
                e.stopPropagation();
                const open = menu.hidden;
                menu.hidden = !open;
                dd.classList.toggle('open', open);
            };
            menu.querySelectorAll('.dd-item').forEach(b => {
                b.onclick = e => {
                    e.stopPropagation();
                    menu.hidden = true;
                    dd.classList.remove('open');
                    if (b.dataset.curdisp) {
                        const v = b.dataset.curdisp;
                        if (v === 'XY') {
                            curLocalXY = true;
                            if (setup && setup.cursor) setup.cursor.display = 'XY';
                            curRendered = null;
                            renderCursorCard(setup && setup.cursor,
                                waveHit && waveHit.cursors ? waveHit.cursors.vals : null);
                            drawWave();
                        } else {
                            curLocalXY = false;
                            if (cs.display !== v) applyCursor({ display: v });
                        }
                    } else if (b.dataset.cursrc && cs.source !== b.dataset.cursrc) {
                        applyCursor({ source: b.dataset.cursrc });
                    }
                };
            });
        });
    }
    if (!vals) return;
    const set = (k, s) => {
        const el = box.querySelector(`[data-v="${k}"]`);
        if (el && el.textContent !== s) el.textContent = s;
    };
    set('ax', fmtSig(vals.tA, 's'));
    set('bx', fmtSig(vals.tB, 's'));
    set('ay', fmtSig(vals.vA, 'V'));
    set('by', fmtSig(vals.vB, 'V'));
    set('dx', fmtSig(vals.dt, 's'));
    set('ix', Number.isFinite(vals.hz) ? fmtSig(vals.hz, 'Hz') : '—');
    set('dy', fmtSig(vals.dv, 'V'));
}

function noteCur(msg, bad) {
    const el = $('curNote');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'trig-note hint' + (bad ? ' bad' : '');
    if (msg && !bad) {
        setTimeout(() => { if (el.textContent === msg) noteCur(''); }, 3000);
    }
}

/* ── 记录页 ─────────────────────────────────────────────────── */
async function initRecord() {
    const cfg = await api('/api/config');
    setConn(cfg.mode);
    $('recRoot').textContent = cfg.record_dir || '—';
    $('btnRecord').onclick = () => doRecord('/api/record');
    $('btnShot').onclick = () => doRecord('/api/shot');
    $('btnOpen').onclick = () => openDir(null);
    pollTrig();
    listFiles();
}

async function pollTrig() {
    try {
        const s = await api('/api/state');
        setConn(s.mode);
        const ready = s.trig.raw === 'STOP';
        const box = $('trigBox');
        box.className = 'trig-box' + (ready ? ' ready' : '');
        if (s.link_err) {
            box.innerHTML = `读不到仪器：<b>${esc(s.link_err)}</b>`;
        } else {
            box.innerHTML = ready
                ? '✓ 已冻结（STOP）—— 可以记录'
                : `仪器当前 <b>${esc(s.trig.raw || '?')}</b>${s.trig.cn ? '（' + esc(s.trig.cn) + '）' : ''}，未冻结。先在面板按 <b>STOP</b> 冻结画面。`;
        }
        $('btnRecord').disabled = !ready || !!s.link_err;
        $('recRoot').textContent = s.record_dir || $('recRoot').textContent;
    } catch (e) {
        $('trigBox').textContent = '读不到仪器状态：' + e.message;
    }
    setTimeout(pollTrig, 2000);
}

async function doRecord(path) {
    $('btnRecord').disabled = $('btnShot').disabled = true;
    $('resultBox').textContent = '记录中…（截图经 VXI-11 回传，几秒）';
    try {
        const r = await api(path, { method: 'POST', body: JSON.stringify({ tag: $('tag').value }) });
        if (!r.ok) {
            $('resultBox').innerHTML = `<b>没有记录：</b>${esc(r.msg || '')}`;
        } else {
            $('resultBox').innerHTML = `✓ 已落盘 ${r.files.length} 个文件 → <span class="num">${esc(r.record_dir)}</span>`;
            listFiles();
        }
    } catch (e) {
        $('resultBox').textContent = '记录失败：' + e.message;
    }
    $('btnShot').disabled = false;
    pollTrig();
}

async function listFiles() {
    try {
        const r = await api('/api/files');
        $('fileCount').textContent = r.files.length;
        const tb = $('fileTable');
        if (!r.files.length) {
            tb.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:32px">还没有记录 —— 面板按 STOP 冻结后点「开始记录」</td></tr>';
            return;
        }
        tb.innerHTML = r.files.map(f => `<tr>
            <td class="fname">${esc(f.name)}</td>
            <td class="nowrap">${f.name.endsWith('.png') ? 'PNG 截图' : 'CSV 波形'}</td>
            <td class="num nowrap">${fmtSize(f.size)}</td>
            <td class="num nowrap">${fmtTime(f.mtime)}</td>
            <td><button class="btn sm" data-day="${esc(f.day)}">打开</button></td></tr>`).join('');
        tb.querySelectorAll('button').forEach(b => b.onclick = () => openDir(b.dataset.day));
    } catch (e) { /* 列表失败不影响记录 */ }
}

async function openDir(day) {
    try {
        await api('/api/open-dir', { method: 'POST', body: JSON.stringify({ day: day || null }) });
    } catch (e) {
        $('resultBox').textContent = '打不开文件夹：' + e.message;
    }
}

/* ── 仪器状态（导航栏）─────────────────────────────────────────
   /api/state 不带 full 只查两条只读命令（触发状态 + 错误队列），15 秒一次。
   错误队列正常时整块隐藏 —— 这是"出问题才看"的信息，不该常驻占版面。 */
async function pollInstrument() {
    try {
        const s = await api('/api/state');
        setConn(s.mode);
        const err = (s.link_err || s.err || '').trim();
        const bad = !!s.link_err || (!!err && !/^[+-]?0\s*,|no error|无错误/i.test(err));
        const pill = $('errPill');
        if (pill) {
            pill.hidden = !bad;
            if (bad) {
                $('errText').textContent = s.link_err ? '断链' : '仪器报错';
                pill.title = s.link_err || err;
            }
        }
    } catch (e) { /* 状态查不到不影响页面 */ }
    setTimeout(pollInstrument, 15000);
}

/* ── 入口 ───────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
    try {
        if ($('wave')) await initStation();
        else if ($('btnRecord')) await initRecord();
        else setConn((await api('/api/config')).mode);
    } catch (e) {
        console.error(e);
    }
    pollInstrument();
});
