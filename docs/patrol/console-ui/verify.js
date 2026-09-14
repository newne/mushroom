/* 无头校验：用 jsdom 跑一遍原型的初始渲染与主要交互，捕获运行期错误。
   用法：node verify.js <prototype.html>

   本文件的站位断言对齐 patrol/stations.py 的现场布局：
   横向 GRID_COLS=12 框（沿 Y 0…4492mm）× 竖向 GRID_LAYERS=5 层（沿 Z -212…0mm，
   第 1 层在最上），每框 1 个角度档 → 60 个站位，层内蛇形遍历。 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');
const errors = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

// 与原型同源的期望值（改布局时这两个数字跟着改，其余断言自动跟着变）
const COLS = 12, LAYERS = 5;
const yOf = c => Math.round((0 + (c - .5) * 4492 / COLS) * 10) / 10;      // col_y
const zOf = L => Math.round((0 - (L - .5) * 212 / LAYERS) * 10) / 10;    // layer_z

function ctxStub() {
  const grad = { addColorStop() { } };
  return new Proxy({}, {
    get(t, k) {
      if (k === 'createLinearGradient' || k === 'createRadialGradient') return () => grad;
      if (k === 'measureText') return () => ({ width: 10 });
      if (k === 'canvas') return { width: 800, height: 600 };
      if (k === 'save' || k === 'restore' || k === 'beginPath' || k === 'fill'
        || k === 'stroke' || k === 'fillRect' || k === 'arc' || k === 'ellipse'
        || k === 'moveTo' || k === 'lineTo' || k === 'fillText' || k === 'clearRect') return () => { };
      return () => { };
    },
    set() { return true; },
  });
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'https://console.local/prototype.html',
  beforeParse(window) {
    window.HTMLCanvasElement.prototype.getContext = function () { return ctxStub(); };
    window.HTMLCanvasElement.prototype.toDataURL = function () {
      return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/wD/AAf/AAAAAElFTkSuQmCC';
    };
    window.onerror = (m, s, l, c) => { errors.push(`onerror: ${m} @${l}:${c}`); };
    window.addEventListener('unhandledrejection', e => errors.push('unhandledrejection: ' + (e.reason && e.reason.message || e.reason)));
    window.console.error = (...a) => errors.push('console.error: ' + a.map(String).join(' '));
    window.console.warn = () => { };
  },
});

const { window } = dom;
const { document } = window;
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const T = s => ($(s) ? ($(s).textContent || '').replace(/\s+/g, ' ').trim() : '<缺失:' + s + '>');
const click = sel => { const el = $(sel); if (!el) throw new Error('找不到元素 ' + sel); el.click(); };

const out = [];
const check = (name, ok, detail = '') => out.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  → ' + detail : ''}`);

(async () => {
  await sleep(400);

  // 1. 初始渲染（实时模式）
  check('站位列表渲染 = 12×5', $$('.stn').length === COLS * LAYERS, `${$$('.stn').length} 行`);
  check('按层分组 = 5 组（不是 60 个单行组）', $$('.boxgroup').length === LAYERS, `${$$('.boxgroup').length} 组`);
  check('层标题带框数与层高', T('#stnList').includes('第 1 层') && T('#stnList').includes('12 框'),
    T('.boxgroup-head'));
  check('状态药丸=待机', T('#pillText') === '待机 · 巡检可调度', T('#pillText'));
  check('顶栏控制器 IP = 192.168.1.239:8088', $('#linkbar').textContent.includes('192.168.1.239:8088'), T('#linkbar'));
  check('中栏有静帧占位', $('#colMain').textContent.includes('最新静帧'));
  check('右栏急停在待机下禁用', $('[data-action="estop"]').disabled === true);
  check('控制按钮在无会话时禁用', $('[data-action="goto"]').disabled === true);
  check('软限位提示 = 机械行程', $('#colOps').textContent.includes('软限位 = 机械行程：Y 0–4492 · Z -212–0'), '');
  check('右栏按轴显示运动参数',
    $('#colOps').textContent.includes('Y 目标 / 回零') && $('#colOps').textContent.includes('Z 目标 / 回零'), '');
  check('操作区不再有未接线的 X 轴控件', $$('[data-axis="X"]').length === 0, `${$$('[data-axis="X"]').length} 个`);
  check('点动方向盘：左右动 Y、上下动 Z',
    !!$('[data-action="jog"][data-axis="Y"][data-dir="1"]') && !!$('[data-action="jog"][data-axis="Z"][data-dir="1"]'), '');
  check('日志有启动事件', $$('#logList .l').length >= 2, `${$$('#logList .l').length} 条`);
  check('日志报出 12×5 布局', $('#logList').textContent.includes('横向 12 框 × 竖向 5 层'), '');
  const toMin = t => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
  const nowHH = new Date(Date.now() + 8 * 3600e3).toISOString().slice(11, 16);
  const firstLog = T('#logList .l .t').slice(0, 5);
  check('日志时间戳按 +08:00 墙上时间', Math.abs(toMin(firstLog) - toMin(nowHH)) <= 2, `${firstLog} vs 本地 ${nowHH}`);

  // 1b. 导轨平面图 = 货架网格本身（12 列 × 5 行）
  const gpos = id => {
    const c = $(`#gmBase .gm-stn[data-id="${id}"] .pt`);
    if (!c) throw new Error('平面图缺少点位 ' + id);
    return { x: Number(c.getAttribute('cx')), y: Number(c.getAttribute('cy')) };
  };
  const gtip = id => {
    const t = $(`#gmBase .gm-stn[data-id="${id}"] title`);
    return t ? t.textContent.replace(/\s+/g, ' ').trim() : '';
  };
  check('平面图站位点数 = 12×5', $$('#gmBase .gm-stn').length === COLS * LAYERS,
    `${$$('#gmBase .gm-stn').length} 点`);
  check('平面图 12×5 格线（11 竖 + 4 横）', $$('#gmGrid .gm-cellline').length === (COLS - 1) + (LAYERS - 1),
    `${$$('#gmGrid .gm-cellline').length} 条`);
  check('平面图层带隔层着色（5 层 → 3 条，便于数层）',
    $$('#gmGrid .gm-band').length === Math.ceil(LAYERS / 2), `${$$('#gmGrid .gm-band').length} 条`);
  check('平面图底部 12 个框号 + 左侧 5 个层号',
    $$('#gmap .gm-ax-col').length === COLS && $$('#gmap .gm-ax-row').length === LAYERS,
    `${$$('#gmap .gm-ax-col').length} 框号 / ${$$('#gmap .gm-ax-row').length} 层号`);
  check('平面图图例 = 角度档 + 失败帧', $$('.maptip i').length === 2, `${$$('.maptip i').length} 项`);
  check('平面图标注机械行程与框/层数',
    T('#mapRange').includes('0–4492') && T('#mapRange').includes('-212–0')
    && T('#mapRange').includes('12 框') && T('#mapRange').includes('5 层'), T('#mapRange'));

  const [g101, g112, g105, g501, g507, g512] =
    ['S101', 'S112', 'S105', 'S501', 'S507', 'S512'].map(gpos);
  check('平面图：Y 大者更靠右（水平长行程）', g112.x > g101.x,
    `S101 x=${g101.x.toFixed(1)} < S112 x=${g112.x.toFixed(1)}`);
  check('平面图：层号大者更靠下（Z 越往下越小，第 1 层在最上）', g501.y > g101.y,
    `S101 y=${g101.y.toFixed(1)} < S501 y=${g501.y.toFixed(1)}`);
  check('平面图：12 列等距分布',
    Math.abs((g112.x - g101.x) / (COLS - 1) - (gpos('S102').x - g101.x)) < 0.6,
    `步距 ${((g112.x - g101.x) / (COLS - 1)).toFixed(2)}`);
  check('平面图：5 层等距分布',
    Math.abs((g501.y - g101.y) / (LAYERS - 1) - (gpos('S201').y - g101.y)) < 0.6,
    `层距 ${((g501.y - g101.y) / (LAYERS - 1)).toFixed(2)}`);

  // 站位必须落在**画出来的**格心上：证明 col_y/layer_z 与格线用的是同一套几何
  const fr = $('#gmGrid .gm-frame');
  const FL = Number(fr.getAttribute('x')), FT = Number(fr.getAttribute('y'));
  const FW = Number(fr.getAttribute('width')), FH = Number(fr.getAttribute('height'));
  check('平面图：站位落在 12×5 格心',
    [['S101', 1, 1], ['S507', 7, 5], ['S312', 12, 3], ['S212', 12, 2]].every(([id, c, l]) =>
      Math.abs(gpos(id).x - (FL + FW * (c - .5) / COLS)) < 0.35
      && Math.abs(gpos(id).y - (FT + FH * (l - .5) / LAYERS)) < 0.35), '');

  // 坐标 = 行程均分（Y 4492/12、Z 212/5），与 stations.col_y / layer_z 同值
  check(`层1框1 坐标 ${yOf(1)} / ${zOf(1)}`, gtip('S101').includes(`Y ${yOf(1)} / Z ${zOf(1)}`), gtip('S101'));
  check(`层5框12 坐标 ${yOf(12)} / ${zOf(5)}`, gtip('S512').includes(`Y ${yOf(12)} / Z ${zOf(5)}`), gtip('S512'));

  check('平面图：点全部落在绘图区内',
    [g101, g501, g112, g512].every(p => p.x >= FL && p.x <= FL + FW && p.y >= FT && p.y <= FT + FH),
    `x ${g101.x.toFixed(1)}–${g112.x.toFixed(1)} / y ${g101.y.toFixed(1)}–${g501.y.toFixed(1)}`);
  check('平面图失败帧标红', $$('#gmBase .gm-stn.has-fail').length >= 1,
    `${$$('#gmBase .gm-stn.has-fail').length} 个`);
  check('平面图标签不越出绘图区',
    $$('#gmBase .lbl').every(t => { const v = Number(t.getAttribute('x')); return v >= FL && v <= FL + FW; }),
    `${$$('#gmBase .lbl').length} 个标签`);
  check('实时模式显示当前位置十字', $('#gmLive').style.display !== 'none', `display="${$('#gmLive').style.display}"`);

  // 蛇形遍历路径：层内横向、换层同列 —— 横向空程为 0（省掉每层 ~4.1m 折返）
  const pathPts = ($('#gmBase .gm-path').getAttribute('points') || '').trim()
    .split(/\s+/).map(p => p.split(',').map(Number));
  check('蛇形路径点数 = 站位数', pathPts.length === COLS * LAYERS, `${pathPts.length} 点`);
  let diagonal = 0;
  for (let i = 1; i < pathPts.length; i++) {
    const sameRow = Math.abs(pathPts[i][1] - pathPts[i - 1][1]) < 0.01;
    const sameCol = Math.abs(pathPts[i][0] - pathPts[i - 1][0]) < 0.01;
    if (!sameRow && !sameCol) diagonal++;
  }
  check('蛇形路径无斜向段（换层只在同列，横向空程 0）', diagonal === 0, `${diagonal} 段斜向`);

  // 2. 越界校验（浏览器侧拦截）：Y 上限 4492、Z 上限 0
  $('#inY').value = '5000';
  $('#inY').dispatchEvent(new window.Event('input', { bubbles: true }));
  check('Y 越界输入被标红', $('#inY').classList.contains('bad'));
  check('越界提示文案', $('#envErr').textContent.includes('超出软限位'), $('#envErr').textContent);
  check('Y 越界时 goto 被禁用', $('[data-action="goto"]').disabled === true, '');
  $('#inY').value = '1900';
  $('#inY').dispatchEvent(new window.Event('input', { bubbles: true }));
  check('合法输入解除拦截', !$('#inY').classList.contains('bad'));
  $('#inZ').value = '50';
  $('#inZ').dispatchEvent(new window.Event('input', { bubbles: true }));
  check('Z 越界（>0）被标红', $('#inZ').classList.contains('bad'));
  $('#inZ').value = '-180';
  $('#inZ').dispatchEvent(new window.Event('input', { bubbles: true }));
  check('Z 合法输入解除拦截', !$('#inZ').classList.contains('bad'));

  // 3. 巡检进行中 → 让位流程（确定性：本轮第一次让位被拒、重试即成功）
  $('#patrolToggle').checked = true;
  $('#patrolToggle').dispatchEvent(new window.Event('change', { bubbles: true }));
  click('#modeSeg [data-mode="realtime"]');
  await sleep(700);
  check('让位中状态可见', ['让位中'].includes(T('#pillText')) || T('#pillText').includes('手动'), T('#pillText'));

  let waited = 0;
  while (waited < 30000 && T('#pillText') !== '手动 · MANUAL') { await sleep(300); waited += 300; }
  check('进入 MANUAL 会话', T('#pillText') === '手动 · MANUAL', `${T('#pillText')}（等待 ${waited}ms）`);
  check('会话倒计时显示', /^\d+:\d\d$/.test(T('#sessLeft')), T('#sessLeft'));
  check('急停可用', $('[data-action="estop"]').disabled === false);
  check('让位日志存在', $('#logList').textContent.includes('yielded') || $('#logList').textContent.includes('让位'), '');

  // 3b. 平面图点击 → 与左栏联动（L1C3 = S103）
  $('#gmBase .gm-stn[data-id="S103"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await sleep(100);
  check('点平面图选中站位', $('.stn[data-id="S103"]').getAttribute('aria-selected') === 'true', '');
  check('平面图选中态同步', $('#gmBase .gm-stn[data-id="S103"]').getAttribute('aria-selected') === 'true', '');
  check('选中坐标写入输入框', $('#inY').value === String(yOf(3)) && $('#inZ').value === String(zOf(1)),
    `${$('#inY').value}/${$('#inZ').value}`);
  check('选中后出现余程连线', $('#gmLink').style.display !== 'none', '');

  // 4. 选站位 → goto（二次确认）；从 L1C1 走到 L1C5
  click('.stn[data-id="S105"]');
  await sleep(60);
  check('选中站位写入坐标', $('#inY').value === String(yOf(5)) && $('#inZ').value === String(zOf(1)),
    `${$('#inY').value}/${$('#inZ').value}`);
  click('[data-action="goto"]');
  await sleep(80);
  check('弹出二次确认', $('#modalRoot').classList.contains('open'));
  check('确认文案含距离', $('#modalBox').textContent.includes('直线距离'), '');
  click('[data-action="modal-ok"]');
  await sleep(400);
  check('运动进行中（进度条>0）', parseFloat($('#jobBar').style.width || '0') > 0, $('#jobBar').style.width);
  check('运动中控制按钮被禁用', $('#colOps [data-action="capture"]').disabled === true, '');
  const midX = Number($('#gmDot').getAttribute('cx'));
  check('平面图十字随运动前移', midX > g101.x + 0.5 && midX < g105.x,
    `${g101.x.toFixed(1)} → ${midX.toFixed(1)} → ${g105.x.toFixed(1)}`);
  await sleep(2800);
  const pos = T('#posRead');
  check(`到位（Y≈${yOf(5)}）`, pos.startsWith(String(yOf(5))), pos);
  check('平面图十字落到目标站位', Math.abs(Number($('#gmDot').getAttribute('cx')) - g105.x) < 1.5,
    `十字 x=${Number($('#gmDot').getAttribute('cx')).toFixed(1)} vs 站位 x=${g105.x.toFixed(1)}`);
  check('运动完成日志', $('#logList').textContent.includes('goto 完成'), '');

  // 5. 抓拍 → 图像索引新增一行 + 日志
  const before = $$('#logList .l').length;
  click('[data-action="capture"]');
  await sleep(4200);
  check('抓拍后出现采图日志', $('#logList').textContent.includes('采图成功 S105'), '');
  check('日志条目增加', $$('#logList .l').length > before, `${before} → ${$$('#logList .l').length}`);
  check('静帧标题指向 S105', $('#colMain').textContent.includes('S105'), '');

  // 6. 点动（Y 轴右移为正）
  const beforeY = parseFloat(T('#posRead'));
  click('[data-action="jog"][data-axis="Y"][data-dir="1"]');
  await sleep(1200);
  check('点动使 Y 增大', parseFloat(T('#posRead')) > beforeY, `${beforeY} → ${T('#posRead')}`);

  // 7. 退出（先回零）
  click('[data-action="exit"]');
  await sleep(3200);
  check('退出后回到待机', T('#pillText') === '待机 · 巡检可调度', T('#pillText'));
  check('回零到原点（Y 0 / Z 0）', T('#posRead').startsWith('0 / 0'), T('#posRead'));

  // 8. 历史模式
  click('#modeSeg [data-mode="history"]');
  await sleep(200);
  click('.stn[data-id="S105"]');
  await sleep(300);
  check('历史模式隐藏当前位置十字', $('#gmLive').style.display === 'none', `display="${$('#gmLive').style.display}"`);
  check('历史模式时间轴渲染', $$('.tl-item').length >= 5, `${$$('.tl-item').length} 帧`);
  check('失败帧有标记', $$('.tl-item.failed').length >= 1, `${$$('.tl-item.failed').length} 帧失败`);
  check('巡采图时间按本地时刻（09:17:50）', $('#colMain').textContent.includes('09:17:50'), '');
  check('来源标签（本地待同步 / prod）', /本地待同步|prod/.test($('#colMain').textContent), '');
  check('手动抓拍帧标记为待同步', $('#colMain').textContent.includes('本地待同步'), '');
  check('运动期间控制按钮禁用', (() => {
    // 已回到待机：控制类按钮应全部禁用
    return $$('#colOps [data-action="goto"],#colOps [data-action="capture"]').every(b => b.disabled);
  })(), '');
  check('生长曲线 SVG', !!$('#colMain svg'), '');
  check('曲线数据点', $$('#colMain svg circle').length >= 12, `${$$('#colMain svg circle').length} 点`);
  const sovg = $('[data-action="zoom"][data-v="4"]');
  sovg.click(); await sleep(150);
  check('4× 缩放生效', $('#colMain').textContent.includes('4×'), '');

  // 9. 筛选：角度档（一期只有斜拍 45°，选项不该出现空档）
  check('角度档只列实际存在的档位（全部 + 斜拍 45°）',
    $$('[data-group="angle"] button').length === 2, `${$$('[data-group="angle"] button').length} 个`);
  click('[data-action="angle"][data-v="top45"]');
  await sleep(200);
  click('.stn[data-id="S105"]');
  await sleep(200);
  check('角度筛选生效', $$('.tl-item').length >= 1 && $$('.tl-item').length <= 7, `${$$('.tl-item').length} 帧`);

  // 10. 急停链路（重复进入实时模式：确定性走一次 409 让位重试 → 成功）
  click('#modeSeg [data-mode="realtime"]');
  let waited2 = 0;
  while (waited2 < 25000 && T('#pillText') !== '手动 · MANUAL') { await sleep(250); waited2 += 250; }
  check('重新进入 MANUAL（含让位重试）', T('#pillText') === '手动 · MANUAL', `${T('#pillText')}（${waited2}ms）`);
  check('409 让位拒绝留痕', $('#logList').textContent.includes('round_in_progress'), '');
  click('[data-action="estop"]');
  await sleep(300);
  check('急停后状态=fault', T('#pillText') === '已急停', T('#pillText'));
  check('出现复位按钮', !!$('[data-action="reset"]'));
  click('[data-action="reset"]');
  await sleep(200);
  check('复位回到待机', T('#pillText') === '待机 · 巡检可调度', T('#pillText'));

  console.log(out.join('\n'));
  console.log('\n运行期错误：' + (errors.length ? '\n  ' + errors.join('\n  ') : '无'));
  const fails = out.filter(l => l.startsWith('FAIL')).length;
  console.log(`\n结果：${out.length - fails}/${out.length} 通过`);
  dom.window.close();
  process.exit(fails || errors.length ? 1 : 0);
})().catch(e => {
  console.log(out.join('\n'));
  console.error('\n执行中断：', e && e.stack || e);
  console.error('运行期错误：', errors.join('\n'));
  process.exit(2);
});
