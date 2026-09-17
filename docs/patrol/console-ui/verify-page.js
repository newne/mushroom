/* 无头校验：用 jsdom 跑一遍**上机页面**（web/console/index.html）的手动控制面。
   用法：node verify-page.js ../../web/console/index.html

   与 verify.js（原型）的分工：原型那份校验的是设计与信息架构；这份校验的是
   **发出去的指令对不对、拒绝有没有显示出来**——页面是能真的把机构动起来的，
   所以这里每一条断言都对着一个"发错就出事"或"没说清就误判"的点。

   本仓库不带 node 工具链（spec §8：零构建、纯 Python），所以 jsdom 用 NODE_PATH
   指向任意一份已装的即可：
     NODE_PATH=/d/code/energy-agent/console/node_modules node verify-page.js <页面> */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const htmlPath = process.argv[2];
if (!htmlPath) { console.error('用法：node verify-page.js <页面 html>'); process.exit(2); }
const html = fs.readFileSync(htmlPath, 'utf8');

const errors = [];
const out = [];
const check = (name, ok, detail = '') => out.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  → ' + detail : ''}`);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------- 假后端：说话方式与 deploy/console.py 一致 ----------
const NOW = '2026-09-14T10:00:00';
const STATIONS = [
  { id: 'S101', box_id: 'B101', y: 187.1, z: 21.2, layer: 1, col: 1, angle_profile: 'top45',
    camera_ip: '192.168.1.238', trim_y: 0, trim_z: 0, target_y: 187.1, target_z: 21.2 },
  { id: 'S102', box_id: 'B102', y: 561.5, z: 21.2, layer: 1, col: 2, angle_profile: 'top45',
    camera_ip: '192.168.1.238', trim_y: 0, trim_z: 0, target_y: 561.5, target_z: 21.2 },
];
const GRID = { cols: 12, layers: 5, y_min: 0.0, y_max: 4492.0, z_min: 0.0, z_max: 212.0 };

// 历史模式用：两天的帧 + 一帧失败 + 一条本地待同步；两条生长点（都要有数值才画得出线）
const IMAGES = [
  { ts: '2026-09-14T09:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top45',
    ok: true, object_name: '20260914/B102_S102_top45_093000', cloud_url: 'http://minio/a.jpg',
    source: 'prod' },
  { ts: '2026-09-14T06:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top45',
    ok: false, object_name: null, cloud_url: '', source: 'prod' },
  { ts: '2026-09-13T09:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top0',
    ok: true, object_name: '20260913/B102_S102_top0_093000', cloud_url: 'http://minio/b.jpg',
    source: 'local' },
];
const GROWTH = [
  { ts: '2026-09-13T09:30:00', mean_len_mm: 28.4, mean_cap_mm: 12.1, verdict: 'no_prev',
    verdict_text: '没有上一个时间点，先作为基线' },
  { ts: '2026-09-14T09:30:00', mean_len_mm: 31.2, mean_cap_mm: 13.6, verdict: 'growing',
    verdict_text: '比上一个时间点长 2.8 mm（+9.9%），在长' },
];

const state = {
  calls: [],                                  // {method, url, body}
  estop: false,
  session: null,                              // {token, opened_at, expires_at}
  cmd: null,                                  // 在飞的那条
  result: null,
  progress: null,                             // 执行方写回的运动实时位置/速度
  patrol_active: false,
  reject_cmd: null,                           // 覆盖 POST /api/cmd 的状态码（测拒绝显示）
  growth_error: false,                        // 让 /api/growth 回"prod 查不到"
  preview_down: false,                        // 让 /api/preview/status 回"预览服务连不上"
  preview_frame_age: 0.4,                     // 上游"最新一帧多久之前"（>3 秒 = 卡住）
  images_error: false,
  image_delay: {},
  command_network_error: false,
  machine_position: [200.0, -20.0],
  machine_pos_source: 'controller',
  // 真执行方会在几百毫秒内领走并写回结果；默认照做，否则页面会一直停在"执行中"，
  // 后面的用例就全被上锁挡住了（那是假后端的锅，不是页面的）。
  auto_complete_ms: 150,
};

function completeSoon() {
  if (!state.auto_complete_ms) return;
  const cmd = state.cmd;
  setTimeout(() => {
    if (state.cmd !== cmd) return;
    const data = {};
    if (cmd.kind === 'goto') data.position_yz = [cmd.args.y, cmd.args.z];
    if (cmd.kind === 'home') data.position_yz = [0, 0];
    state.result = { id: cmd.id, kind: cmd.kind, args: cmd.args, data,
      ok: true, detail: '已执行（假后端）', ended_at: NOW };
    state.cmd = null;
    state.progress = null;                    // 结果落地，实时流收场（与执行方一致）
  }, state.auto_complete_ms);
}

function json(status, body) { return { status, ok: status < 400, json: async () => body }; }

function route(method, url, body) {
  state.calls.push({ method, url, body });
  const path = url.split('?')[0];
  if (path === '/api/stations') {
    return json(200, { ok: true, grid: GRID, grid_angles: ['top45'], stations: STATIONS });
  }
  if (path === '/api/status') {
    return json(200, {
      ts: NOW,
      machine: { state: state.patrol_active ? 'PATROLLING' : 'IDLE', connected: true,
                 real_pos: state.machine_position, pos_source: state.machine_pos_source, probe_suppressed: false,
                 host: '172.17.0.1:7003' },
      patrol: state.patrol_active
        ? { active: true, known: true, started_at: '2026-09-14T09:58:00', station_index: 12,
            station_total: 60, current_station: 'S105', reason: '正在巡检（守护占着控制器）' }
        : { active: false, known: true, status: 'ok', n_results: 60, reason: '上一轮已结束（ok）' },
      room: { ok: true, allowed: true, room_id: '611', entry_date: '2026-09-04', day: 10,
              text: '第 10 天：巡检' },
      envelope: { y: [0, 4492], z: [-212, 0] },
      events: [{ ts: NOW, level: 'info', text: '页面已连接' }],
    });
  }
  if (path === '/api/images') {
    if (state.images_error) return json(500, { error: 'boom' });
    const stationId = new URL('http://console.local' + url).searchParams.get('station_id');
    const rows = IMAGES.map(r => ({ ...r, station_id: stationId || r.station_id }));
    const response = json(200, { ok: true, rows, n_local: 1, n_prod: IMAGES.length - 1 });
    const delay = state.image_delay[stationId] || 0;
    return delay ? new Promise(resolve => setTimeout(() => resolve(response), delay)) : response;
  }
  if (path === '/api/growth') {
    if (state.growth_error) return json(200, { ok: false, error: 'prod 查询失败：不通', points: [] });
    return json(200, { ok: true, box_id: 'B102', points: GROWTH,
                       latest: GROWTH[GROWTH.length - 1] });
  }
  if (path === '/api/preview/status') {
    // 与 deploy/console.py 的 /api/preview/status 同形状：available + 拒绝理由 + 上游健康
    if (state.patrol_active) {
      return json(200, { available: false, reason: 'patrolling',
                         error: '巡检进行中：相机这一路归本轮，预计 7 分钟后可用',
                         retry_after_s: 420, upstream: null });
    }
    if (state.preview_down) {
      return json(200, { available: false, upstream: null, error: '预览服务连不上：preview 没起' });
    }
    // 键名与 deploy/preview.py 的 Broadcaster.status() 一致：last_frame_age_s
    return json(200, { available: true, error: null,
                       upstream: { ok: true, frames: 42, viewers: 1,
                                   last_frame_age_s: state.preview_frame_age } });
  }
  if (path === '/api/cmd' && method === 'GET') {
    if (state.command_network_error) throw new Error('offline');
    return json(200, { inflight: state.cmd, result: state.result, progress: state.progress,
                       estop: state.estop, session_active: !!state.session });
  }
  if (path === '/api/session' && method === 'POST') {
    state.session = { token: 't-1', opened_at: NOW, expires_at: '2026-09-14T10:05:00' };
    return json(201, { session: state.session, ttl_s: 300 });
  }
  if (path === '/api/session' && method === 'DELETE') {
    state.session = null;
    return json(200, { closed: true, home_command: { id: 'c-9', kind: 'home' },
                       detail: '已排回零：执行方到位后写回结果' });
  }
  if (path === '/api/stop' && method === 'POST') { state.estop = true; return json(200, { estop: true }); }
  if (path === '/api/stop' && method === 'DELETE') { state.estop = false; return json(200, { estop: false }); }
  if (path === '/api/cmd' && method === 'POST') {
    if (state.command_network_error) throw new Error('offline');
    if (state.reject_cmd) return json(state.reject_cmd, { error: state.reject_cmd === 409
      ? '巡检进行中：机构由这一轮占用，预计 7 分钟后可用' : '没有有效会话：手动移动需要先接管' });
    state.cmd = { id: 'c-' + (state.calls.length), kind: body.kind, args: body.args || {}, by: body.by,
                  session: state.session ? state.session.token : '', started_at: null };
    state.result = null;
    completeSoon();
    return json(202, { command: state.cmd, detail: '已提交',
                       session: state.session ? { ...state.session, expires_at: '2026-09-14T10:09:00' } : null });
  }
  return json(404, { error: 'no route ' + path });
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'http://console.local/',
  beforeParse(window) {
    window.fetch = async (url, opt = {}) => route(opt.method || 'GET', String(url),
      opt.body ? JSON.parse(opt.body) : undefined);
    window.confirm = message => { window.__confirmed.push(true); window.__confirmMessages.push(String(message)); return window.__ok; };
    window.__confirmed = [];
    window.__confirmMessages = [];
    window.__ok = true;
    window.onerror = (m, s, l, c) => { errors.push(`onerror: ${m} @${l}:${c}`); };
    window.addEventListener('unhandledrejection',
      e => errors.push('unhandledrejection: ' + ((e.reason && e.reason.message) || e.reason)));
    window.console.error = (...a) => errors.push('console.error: ' + a.map(String).join(' '));
  },
});

const { window } = dom;
const { document } = window;
const $ = s => document.querySelector(s);
const T = s => ($(s) ? ($(s).textContent || '').replace(/\s+/g, ' ').trim() : '<缺失:' + s + '>');
// 点击后等一拍：页面提交 → 假后端在 150ms 后写回结果 → 页面还要一次 /api/cmd 轮询
// 才知道"空闲了"（有指令在飞时是 300ms 加密轮询，空闲时是 1s 的 tick）。不等的话
// 第二次点击会落进"上一条还在执行"的上锁窗口（假后端的时序，不是页面的错）。
const click = async sel => {
  const el = $(sel);
  if (!el) throw new Error('找不到元素 ' + sel);
  el.click();
  await sleep(1200);
};
const lastCall = p => [...state.calls].reverse().find(c => c.url.split('?')[0] === p);
// 只看**发出去的指令**：页面每次提交后都会紧跟着 GET /api/cmd，取"最后一条同路径"
// 会拿到那次轮询（这正是本文件第一版踩过的坑）。
const lastCmd = () => {
  const c = [...state.calls].reverse()
    .find(c => c.url.split('?')[0] === '/api/cmd' && c.method === 'POST');
  return c ? c.body : null;
};

(async () => {
  await sleep(400);

  // 1. 初始（未接管、无巡检）
  check('页面渲染出站位列表', document.querySelectorAll('.stn').length === STATIONS.length,
    document.querySelectorAll('.stn').length + ' 行');
  check('急停按钮存在且**未接管时也可用**', !!$('#estopbtn') && $('#estopbtn').disabled === false);
  check('未接管时点动/定位/回零/抓拍都禁用',
    ['#gotobtn', '#homebtn', '#capbtn'].every(s => $(s).disabled) &&
    Array.from(document.querySelectorAll('[data-jog]')).every(b => b.disabled));
  check('未接管时给出接管提示', T('#sessionline').includes('未接管'), T('#sessionline'));

  // 1a. 坐标框架（ADR-0018）：Z 的原点在**顶端**、向下为正。
  //     平面图必须把第 1 层画在最上面——旧代码按 z_max 起算，把第 1 层画到了最下面，
  //     而"层画反了"与"机器去错层"是同一个错的两个面。
  check('平面图标明 Z 向下', html.includes('Z 向下'), 'h2=' + T('section h2'));
  check('坐标范围文案来自 /api/grid 且说明 Z 向下',
    T('#envlbl').includes('Z 0…212') && T('#envlbl').includes('向下'), T('#envlbl'));
  const dots = Array.from(document.querySelectorAll('#map circle'));
  const yOf = id => Number(dots.find(c => c.dataset.id === id)?.getAttribute('cy'));
  check('第 1 层的点画在图的上半部（Z 原点在顶端）', yOf('S101') < 100, 'cy=' + yOf('S101'));
  check('点动按钮标出物理方向（Z + 是向下）',
    T('[data-jog="Z+"]').includes('下') && T('[data-jog="Z-"]').includes('上'),
    T('[data-jog="Z+"]') + ' / ' + T('[data-jog="Z-"]'));

  // 1c. 导轨图（2026-09-17 改版）：放大移到**中栏**，滑台两轴自由移动 ⇒ 点击任意位置设目标
  check('导轨图在中栏（左栏只留站位列表）',
    !!$('#rtmain #map') && !document.querySelector('main>section:first-child #map'),
    $('#rtmain #map') ? '中栏 ✓' : '不在中栏');
  check('站点 tooltip 给的是毫米坐标（两位小数），不是 SVG 像素',
    (dots.find(c => c.dataset.id === 'S101')?.querySelector('title')?.textContent || '')
      .includes('Y=187.10 Z=21.20 mm'),
    dots.find(c => c.dataset.id === 'S101')?.querySelector('title')?.textContent || '(无 title)');
  check('站位列表坐标也是两位小数', T('.stn .mono').includes('187.10, 21.20'), T('.stn .mono'));

  // 点击空白处 = 填坐标（不发指令！移动永远走「移动到该点」+二次确认）；
  // jsdom 不做坐标命中，补一个 getBoundingClientRect 让换算有输入。
  const mapsvg = $('#map');
  mapsvg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 920, height: 170 });
  state.calls.length = 0;
  mapsvg.dispatchEvent(new window.MouseEvent('click', { clientX: 460, clientY: 85, bubbles: true }));
  await sleep(120);
  check('地图空白处点击填入两位小数坐标', $('#gtY').value === '2210.35' && $('#gtZ').value === '112.33',
    $('#gtY').value + ' / ' + $('#gtZ').value);
  check('地图点击**不直接发指令**', lastCmd() === null || lastCmd() === undefined,
    JSON.stringify(lastCmd()));
  check('待定目标画出了虚线框', !!document.querySelector('#map .map-pending'));
  mapsvg.dispatchEvent(new window.MouseEvent('mousemove', { clientX: 460, clientY: 85, bubbles: true }));
  check('悬停显示光标处坐标', T('#maphover').includes('Y=2210.35') && T('#maphover').includes('Z=112.33'),
    T('#maphover'));
  // 收尾：别把坐标/选中态带进后面的用例（历史模式断言"未选站位"的提示）
  $('#gtY').value = ''; $('#gtZ').value = '';
  $('#gtY').dispatchEvent(new window.Event('input'));
  mapsvg.dispatchEvent(new window.MouseEvent('mouseleave', { bubbles: true }));

  // 1b. 历史模式：**还没选站位**时该给提示而不是空白表格
  await click('#modeSeg [data-mode="history"]');
  check('历史模式：操作区让位给筛选', $('#rtcol').style.display === 'none' && $('#hcol').style.display === '');
  check('历史模式：中栏换成时间轴/大图/曲线',
    $('#hmain').style.display === '' && $('#rtmain').style.display === 'none');
  check('未选站位时时间轴给的是提示', T('#timeline').includes('点左侧'), T('#timeline'));
  check('未选站位时不画曲线', T('#curve').includes('选一个站位'), T('#curve'));
  await click('#modeSeg [data-mode="realtime"]');
  check('切回实时模式：操作区回来', $('#rtcol').style.display === '' && $('#rtmain').style.display === '');

  // 2. 接管 → 解锁
  await click('#takebtn');
  await sleep(200);
  check('接管后会话生效', T('#sessionline').includes('已接管'), T('#sessionline'));
  check('接管后有倒计时（mm:ss）', /\d\d:\d\d/.test(T('#sessionline')), T('#sessionline'));
  check('接管后回零按钮可用', $('#homebtn').disabled === false);
  check('接管后点动按钮可用',
    Array.from(document.querySelectorAll('[data-jog]')).every(b => !b.disabled));

  // 3. 点动的载荷（发错轴/发错符号 = 机构往反方向走）
  state.calls.length = 0;
  await click('[data-jog="Y+"]');
  check('Y+ 点动载荷 = {axis:Y, mm:+5}',
    JSON.stringify(lastCmd()) === '{"kind":"jog","args":{"axis":"Y","mm":5}}', JSON.stringify(lastCmd()));
  await click('[data-jog="Z-"]');
  check('Z− 点动载荷 = {axis:Z, mm:−5}',
    JSON.stringify(lastCmd()) === '{"kind":"jog","args":{"axis":"Z","mm":-5}}', JSON.stringify(lastCmd()));
  $('#step').value = '0.5';
  await click('[data-jog="Y-"]');
  check('步长切到 0.5 生效', JSON.stringify(lastCmd()) === '{"kind":"jog","args":{"axis":"Y","mm":-0.5}}',
    JSON.stringify(lastCmd()));

  // 补光灯开关已从页面删除（现场无实际作用）——连 DOM 都不该再有
  check('页面上没有补光灯开关（无实际作用，2026-09-17 删除）', !$('#lampbtn') && !html.includes('lampbtn'));

  // 4. 定位：二次确认 + 载荷
  state.calls.length = 0;
  state.machine_position = null;
  state.machine_pos_source = 'unknown';
  state.result = null;
  await sleep(1100);
  $('#gtY').value = '1200'; $('#gtZ').value = '100';
  $('#gtY').dispatchEvent(new window.Event('input'));
  window.__ok = true; window.__confirmed.length = 0; window.__confirmMessages.length = 0;
  await click('#gotobtn');
  check('定位前有二次确认', window.__confirmed.length === 1);
  check('当前位置未知时不伪造 0 mm 距离', window.__confirmMessages[0].includes('无法计算距离') && !window.__confirmMessages[0].includes('0 mm'),
    window.__confirmMessages[0]);
  check('定位载荷 = {y:1200, z:-100}',
    JSON.stringify(lastCmd()) === '{"kind":"goto","args":{"y":1200,"z":100}}', JSON.stringify(lastCmd()));
  check('结构化位置结果更新可信读数（两位小数）',
    T('#actualpos').includes('Y=1200.00') && T('#actualpos').includes('Z=100.00') && T('#actualpos').includes('最后成功目标'),
    T('#actualpos'));
  state.machine_position = [200.0, -20.0];
  state.machine_pos_source = 'controller';
  await sleep(1100);

  state.calls.length = 0;
  window.__ok = false;                       // 用户在确认框里点了取消
  await click('#gotobtn');
  check('取消确认后**一条都不发**', lastCmd() === null || lastCmd() === undefined);

  // 5. 浏览器侧软限位：越界不发、当场标红
  window.__ok = true;
  state.calls.length = 0;
  $('#gtY').value = '9999';                  // Y 上限 4492
  $('#gtY').dispatchEvent(new window.Event('input'));
  check('越界输入标红', $('#gtY').className === 'bad', $('#gtY').className);
  check('越界时提交按钮禁用', $('#gotobtn').disabled === true);
  await click('#gotobtn');
  check('越界**不下发**', lastCmd() === null || lastCmd() === undefined);
  $('#gtY').value = '1000';
  $('#gtY').dispatchEvent(new window.Event('input'));
  check('回到范围内即恢复可提交', $('#gotobtn').disabled === false && $('#gtY').className === '');

  // 6. 抓拍：没选站位不让点；选了就带上 station_id。
  //    抓拍 2026-09-17 并入实时画面卡（拍的就是"此刻画面里的这个位置"），与「看画面」并列。
  check('未选站位时抓拍禁用', $('#capbtn').disabled === true);
  check('抓拍与「看画面」在同一张卡（实时画面卡）',
    !!$('#rtmain #capbtn') && $('#capbtn').closest('.card') === $('#pvbtn').closest('.card'));
  // 这次从**地图**上点站位（S102 图心 ≈ 136,27，命中圈内）：地图也是选站入口
  mapsvg.dispatchEvent(new window.MouseEvent('click', { clientX: 136, clientY: 27, bubbles: true }));
  await sleep(400);
  check('点地图上的站点 = 选中站位', T('#imgtitle').includes('S102'), T('#imgtitle'));
  check('选中后坐标框填成该站目标（两位小数）', $('#gtY').value === '561.50' && $('#gtZ').value === '21.20',
    $('#gtY').value + ' / ' + $('#gtZ').value);
  check('选中站位后抓拍可用', $('#capbtn').disabled === false);
  state.calls.length = 0;
  await click('#capbtn');
  check('抓拍载荷带 station_id=S102',
    JSON.stringify(lastCmd()) === '{"kind":"capture","args":{"station_id":"S102"}}', JSON.stringify(lastCmd()));

  // 7. 后端拒绝要**原样显示**（403 需要接管 / 409 巡检中）
  state.reject_cmd = 403;
  await click('[data-jog="Y+"]');
  check('403 显示"需要先接管"', T('#opmsg').includes('需要先接管'), T('#opmsg'));
  state.reject_cmd = 409;
  await click('[data-jog="Y+"]');
  check('409 显示后端原话（含还要等多久）',
    T('#opmsg').includes('巡检进行中') && T('#opmsg').includes('分钟后可用'), T('#opmsg'));
  state.reject_cmd = null;

  state.command_network_error = true;
  await click('[data-jog="Y+"]');
  check('控制接口离线时给出可见反馈', T('#opmsg').includes('接口不可达'), T('#opmsg'));
  state.command_network_error = false;

  // 8. 巡检进行中：提前上锁 + 说明还要等多久
  state.patrol_active = true;
  await sleep(1200);
  check('巡检中运动按钮上锁', $('#homebtn').disabled === true &&
    Array.from(document.querySelectorAll('[data-jog]')).every(b => b.disabled));
  check('巡检中提示含预计时间', T('#opnotice').includes('预计') && T('#opnotice').includes('分钟后可用'),
    T('#opnotice'));
  check('巡检中急停**仍然可点**', $('#estopbtn').disabled === false);
  state.patrol_active = false;
  await sleep(1200);
  check('巡检结束后解锁', $('#homebtn').disabled === false);

  // 9. 急停：闩锁 + 复位
  state.calls.length = 0;
  await click('#estopbtn');
  const stopped = state.calls.find(c => c.url.startsWith('/api/stop') && c.method === 'POST');
  check('急停发出 POST /api/stop', !!stopped, JSON.stringify(state.calls.map(c => c.method + ' ' + c.url)));
  await sleep(200);
  check('急停后标签显示已置位', T('#estopstate').includes('已置位'), T('#estopstate'));
  check('急停后出现复位按钮', $('#estopclear').disabled === false);
  await click('#estopclear');
  await sleep(200);
  check('复位后回到未置位', T('#estopstate').includes('未置位'), T('#estopstate'));

  // 10. Esc = 只中断**正在执行**的那一条（空闲时不响应，免得误按闩上急停）
  state.auto_complete_ms = 0;                 // 让指令停在"在飞"，模拟一条长指令
  state.cmd = null; state.result = null;
  await sleep(1200);
  state.calls.length = 0;
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(80);
  check('空闲时按 Esc 不发急停', !state.calls.some(c => c.url.startsWith('/api/stop')));
  state.cmd = { id: 'c-2', kind: 'goto', args: { y: 1200, z: 100 }, started_at: NOW };
  state.result = null;
  await sleep(1200);                          // 等一拍 /api/cmd 轮询把"在飞"读进来
  check('运动中显示"执行中"', T('#cmdline').includes('执行中'), T('#cmdline'));
  state.calls.length = 0;
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(120);
  check('运动中按 Esc 发急停', state.calls.some(c => c.url.startsWith('/api/stop') && c.method === 'POST'));
  state.cmd = null; state.estop = false;
  state.result = { id: 'c-2', ok: false, detail: '等待运动被中止（急停请求）', ended_at: NOW };
  state.auto_complete_ms = 150;
  await sleep(1200);
  check('结果如实显示（失败的原因原样带出）', T('#cmdline').includes('等待运动被中止'), T('#cmdline'));

  // 10b. 运动中的实时位置/速度：执行方随 /api/cmd 写回 progress，页面按它渲染——
  //      但**只有属于在飞那条指令**的进度才算数（id 对不上的是残值，残值更害人）。
  state.auto_complete_ms = 0;
  state.cmd = { id: 'c-live', kind: 'goto', args: { y: 2210.35, z: 111.17 }, started_at: NOW };
  state.result = null;
  state.progress = { id: 'c-live', kind: 'goto', ts: NOW,
                     position_yz: [1105.17, 55.59], speed_yz: [40.0, 0.0], moving: true };
  await sleep(1400);                          // busy ⇒ /api/cmd 按 300ms 加密轮询
  check('实时位置来自执行方写回（标「控制器实时」，两位小数）',
    T('#actualpos').includes('Y=1105.17') && T('#actualpos').includes('Z=55.59') && T('#actualpos').includes('控制器实时'),
    T('#actualpos'));
  check('移动中显示合成速度', T('#cmdstage').includes('移动中') && T('#cmdstage').includes('v=40.0 mm/s'),
    T('#cmdstage'));
  state.progress = { id: 'c-old', kind: 'goto', ts: NOW,
                     position_yz: [0, 0], speed_yz: [0, 0], moving: true };
  await sleep(1200);
  check('进度 id 对不上时不冒充实时位置', !T('#actualpos').includes('控制器实时'), T('#actualpos'));
  state.cmd = null; state.progress = null;
  state.result = { id: 'c-live', ok: true, detail: '已到位（假后端）', ended_at: NOW,
                   data: { position_yz: [2210.35, 111.17] } };
  state.auto_complete_ms = 150;
  await sleep(1200);
  check('到位后实时流收场，回到控制器实读（不再冒充实时）',
    T('#actualpos').includes('Y=200.00') && T('#actualpos').includes('控制器实读') && !T('#actualpos').includes('控制器实时'),
    T('#actualpos'));

  // 11. 放开会话：后端排回零，页面把话说清楚
  await click('#takebtn');
  await sleep(200);
  state.calls.length = 0;
  await click('#dropbtn');
  check('放开会话发出 DELETE /api/session',
    state.calls.some(c => c.url.split('?')[0] === '/api/session' && c.method === 'DELETE'));
  check('放开时说明"已排回零"', T('#opmsg').includes('回零'), T('#opmsg'));

  // 12. 历史模式：时间轴 / 大图 / 生长曲线 / 筛选 / 与 prod 不通的区别
  await click('#modeSeg [data-mode="history"]');
  await click('.stn[data-id="S102"]');
  await sleep(400);
  check('时间轴按日期分组（2 天 → 2 个日期头）',
    document.querySelectorAll('.tlhead').length === 2, document.querySelectorAll('.tlhead').length + ' 个');
  check('时间轴帧数 = 3', document.querySelectorAll('.tl .im').length === 3,
    document.querySelectorAll('.tl .im').length + ' 帧');
  check('失败帧被标出来', T('#timeline').includes('采图失败'));
  check('本地待同步那一帧有标记', !!document.querySelector('.tl .im.local'));
  check('角度档只列数据里有的（全部 + top0 + top45）',
    document.querySelectorAll('#angleSel option').length === 3,
    document.querySelectorAll('#angleSel option').length + ' 项');

  await click('.tl .im[data-pick]');
  check('点缩略图 → 大图有 src', !!$('#bigimg').getAttribute('src'), $('#bigimg').getAttribute('src') || '');
  check('大图默认 1×', T('#zoomlabel').includes('1×'), T('#zoomlabel'));
  await click('[data-zoom="4"]');
  check('4× 缩放生效（transform 里带 scale(4)）',
    ($('#bigimg').style.transform || '').includes('scale(4)'), $('#bigimg').style.transform);
  await click('#fitbtn');
  check('适应复位回 1×', ($('#bigimg').style.transform || '').includes('scale(1)'),
    $('#bigimg').style.transform);

  check('生长曲线画出来了（两条线 + 点）',
    !!$('#curve svg') && document.querySelectorAll('#curve svg path').length === 2,
    document.querySelectorAll('#curve svg path').length + ' 条');
  check('曲线下方列出最近的测量值', T('#growthnote').includes('31.2') && T('#growthnote').includes('在长'),
    T('#growthnote').slice(0, 60));
  check('曲线标题带框号', T('#curvetitle').includes('B102'), T('#curvetitle'));

  // 筛选：日期只留 09-14 → 2 帧；角度只留 top0 → 1 帧
  $('#fromDate').value = '2026-09-14'; $('#fromDate').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('日期筛选生效（09-14 → 2 帧）', document.querySelectorAll('.tl .im').length === 2,
    document.querySelectorAll('.tl .im').length + ' 帧');
  $('#angleSel').value = 'top0'; $('#angleSel').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('筛选后可筛出 0 帧并说明原因', T('#timeline').includes('放宽日期'), T('#timeline').slice(0, 60));
  await click('#clearfilter');
  check('清空筛选后回到 3 帧', document.querySelectorAll('.tl .im').length === 3,
    document.querySelectorAll('.tl .im').length + ' 帧');

  // prod 查不到 vs 还没有测量值：两件事，必须分开说
  state.growth_error = true;
  await click('#reloadbtn');
  await sleep(300);
  check('prod 查不到时如实报错（不是画一条空曲线）',
    T('#curve').includes('查不到') && T('#growthnote').includes('prod'), T('#growthnote'));
  state.growth_error = false;

  // 快速切站时，慢返回的旧图像不能覆盖后选中的站位。
  await click('#modeSeg [data-mode="realtime"]');
  state.image_delay = { S101: 500, S102: 20 };
  document.querySelector('.stn[data-id="S101"]').click();
  await sleep(30);
  document.querySelector('.stn[data-id="S102"]').click();
  await sleep(750);
  check('快速切站不串图像', T('#imgtitle').includes('S102') &&
    Array.from(document.querySelectorAll('#imgs img')).every(img => img.alt.includes('S102')),
    T('#imgtitle') + ' / ' + Array.from(document.querySelectorAll('#imgs img')).map(img => img.alt).join(', '));
  state.image_delay = {};

  // 13. 实时画面（ADR-0017）：接管自动开、放开自动关、轮内不给看
  await click('#modeSeg [data-mode="realtime"]');
  check('页面里没有相机地址/口令，只有 /api/preview（浏览器只连 console）',
    !/192\.168\.|rtsp:|camera_pwd/.test(html), '命中片段：' +
    (html.match(/192\.168\.|rtsp:|camera_pwd/gi) || []).join(',') || '（无）');
  check('实时画面卡在**中栏**（点动按钮在右栏，两者要同屏）',
    !!$('#rtmain #pvimg') && !$('#rtcol #pvimg'),
    $('#rtmain #pvimg') ? '中栏 ✓' : '不在中栏');

  state.preview_down = false;
  await click('#takebtn');
  await sleep(500);
  await click('#modeSeg [data-mode="history"]');
  check('切到历史模式会关闭实时画面', !$('#pvimg').getAttribute('src'), $('#pvstate').textContent);
  await click('#modeSeg [data-mode="realtime"]');
  await sleep(500);
  check('有效会话切回实时会恢复画面', ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');
  check('接管后自动打开画面（<img src="/api/preview">）',
    ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');
  // ⚠️ 这一条是上机踩过的坑：CSS 里 `.pvwrap img{display:none}` 是默认隐藏，
  //    开流时写 `display:''` 只是清掉内联样式，规则照样生效——画面拿到了却只有一个黑框。
  check('播放中 <img> 真的可见（display:block，不是被 CSS 藏住）',
    $('#pvimg').style.display === 'block', 'display=' + ($('#pvimg').style.display || '(空)'));
  check('播放中提示文字让位', $('#pvhint').style.display === 'none',
    'hint display=' + ($('#pvhint').style.display || '(空)'));
  check('画面状态标为播放中', T('#pvstate').includes('播放中'), T('#pvstate'));
  check('画面信息来自 console 的 /api/preview/status（不是直连相机）',
    !!lastCall('/api/preview/status') && !state.calls.some(c => /192\.168|:554/.test(c.url)),
    JSON.stringify([...new Set(state.calls.map(c => c.url.split('?')[0]))].slice(-6)));

  // 流"卡住"（连接还在、没有新帧）浏览器不会报错：页面必须靠上游的帧龄自己发现并重连
  const stalledSrc = $('#pvimg').getAttribute('src');
  state.preview_frame_age = 9;
  await sleep(2600);
  check('画面卡住时自动重连（换一个 src）',
    $('#pvimg').getAttribute('src') !== stalledSrc && T('#pvstate').includes('重连'),
    T('#pvstate') + ' / ' + $('#pvimg').getAttribute('src'));
  state.preview_frame_age = 0.4;
  await sleep(2600);
  check('恢复后重新播放', T('#pvstate').includes('播放中'), T('#pvstate'));

  await click('#dropbtn');
  await sleep(400);
  check('放开接管后画面自动关闭',
    !$('#pvimg').getAttribute('src'), $('#pvimg').getAttribute('src') || '(已清空)');
  check('关掉后 <img> 隐藏、提示文字回来',
    $('#pvimg').style.display === 'none' && $('#pvhint').style.display !== 'none',
    'img display=' + ($('#pvimg').style.display || '(空)'));

  // 轮内：接管也拿不到画面，且要说清"本轮结束后自动恢复"
  state.patrol_active = true;
  await sleep(1400);                          // 等一拍 /api/status 把轮次读进来
  await click('#takebtn');
  await sleep(500);
  check('巡检进行中不开画面', !$('#pvimg').getAttribute('src'),
    $('#pvimg').getAttribute('src') || '(无 src)');
  check('巡检中说明原因与恢复时机',
    T('#pvstate').includes('巡检中') && T('#pvnote').includes('本轮结束后自动恢复'),
    T('#pvstate') + ' / ' + T('#pvnote'));

  state.patrol_active = false;
  await sleep(2600);                          // 巡检结束 → 下一拍自己恢复
  check('本轮结束后画面自动恢复',
    ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');

  // 预览服务不可用：把后端的话原样说出来，而不是"失败"
  state.preview_down = true;
  await sleep(2600);
  check('预览不可用时原样显示后端话术',
    T('#pvhint').includes('连不上') && T('#pvstate').includes('不可用'),
    T('#pvstate') + ' / ' + T('#pvhint'));
  state.preview_down = false;
  await sleep(2600);
  check('预览恢复后又自己接上', ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');

  // 手动开关：接管中也能自己把画面停掉（不想占相机那一路时）
  await click('#pvbtn');
  await sleep(200);
  check('手动"停画面"生效', !$('#pvimg').getAttribute('src') && T('#pvstate').includes('未打开'),
    T('#pvstate'));
  await click('#pvbtn');
  await sleep(400);
  check('手动"看画面"又开起来', ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');

  console.log(out.join('\n'));
  console.log('\n运行期错误：' + (errors.length ? '\n  ' + errors.join('\n  ') : '无'));
  const fails = out.filter(l => l.startsWith('FAIL')).length;
  console.log(`\n结果：${out.length - fails}/${out.length} 通过`);
  dom.window.close();
  process.exit(fails || errors.length ? 1 : 0);
})().catch(e => {
  console.log(out.join('\n'));
  console.error('\n执行中断：', (e && e.stack) || e);
  console.error('运行期错误：', errors.join('\n'));
  process.exit(2);
});
