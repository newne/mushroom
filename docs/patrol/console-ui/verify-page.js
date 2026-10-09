/* 无头校验：用 jsdom 跑一遍**上机页面**（web/console/index.html）的手动控制面。
   用法：node verify-page.js ../../web/console/index.html

   与 verify.js（原型）的分工：原型那份校验的是设计与信息架构；这份校验的是
   **发出去的指令对不对、拒绝有没有显示出来**——页面是能真的把机构动起来的，
   所以这里每一条断言都对着一个"发错就出事"或"没说清就误判"的点。

   本仓库不带 node 工具链（spec §8：零构建、纯 Python），所以 jsdom 用 NODE_PATH
   指向任意一份已装的即可：在本目录（docs/patrol/console-ui/）下

     NODE_PATH=/d/code/energy-agent/console/node_modules \
       node verify-page.js ../../../web/console/index.html

   ⚠️ 页面路径写**绝对路径**最省事：从本目录到仓库根是三级（`../../../`），
   少一级会去开 `docs/web/console/index.html` 并报 ENOENT。 */
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
  { id: 'S412', box_id: 'B412', y: 4300.0, z: 190.0, layer: 4, col: 12, angle_profile: 'top45',
    camera_ip: '192.168.1.238', trim_y: 0, trim_z: 0, target_y: 4300.0, target_z: 190.0 },
];
// 与 patrol.stations.grid_geometry() 同形状：**y_pitch/z_pitch 不能省**——页面的
// nearestStation 按格距归一化后再比站距（见该函数注释），少了这两个字段它会退回
// "直接比毫米"，与假后端的 fakeNearest 口径就不一样了：假件更松，真分歧测不出来。
const GRID = { cols: 12, layers: 4, y_pitch: 4492.0 / 12, z_pitch: 212.0 / 4,
               y_min: 0.0, y_max: 4492.0, z_min: 0.0, z_max: 212.0 };

// 历史模式用：同一批次的两轮、失败帧、本地待同步帧和无 round_id 的旧轮次。
//
// ⚠️ 这里**故意不写 `cloud_url`**：console 的 `/api/images` 已经不再回这个字段
// （它是控制网地址，浏览器够不着）。谁要是又改回"页面直接用 cloud_url 当 <img src>"，
// 这个假件不会喂给它任何地址 → 用例当场红。
const IMAGES = [
  { round_id: 'round-20260914', ts: '2026-09-14T09:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top45',
    ok: true, object_name: '20260914/B102_S102_top45_093000', source: 'prod' },
  { round_id: 'round-20260914', ts: '2026-09-14T06:30:00', station_id: 'S101', box_id: 'B101', angle_profile: 'top0',
    ok: false, object_name: null, source: 'prod' },
  { round_id: 'round-20260913', ts: '2026-09-13T09:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top0',
    ok: true, object_name: '20260913/B102_S102_top0_093000', source: 'local' },
  { round_id: 'round-20260913', ts: '2026-09-13T06:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top45',
    ok: false, object_name: null, source: 'prod' },
];
const ROUNDS = [
  { round_id: 'round-20260914', ts: '2026-09-14T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: false },
  { round_id: 'round-20260913', ts: '2026-09-13T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: false },
  { round_id: null, ts: '2026-09-12T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: true },
  { round_id: 'round-20260911', ts: '2026-09-11T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-09', ok: true },
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
  preview_frames: 42,                         // 上游累计帧计数（只增不减；页面只拿它算 fps 差值）
  images_error: false,
  image_delay: {},
  command_network_error: false,
  machine_position: [200.0, -20.0],
  machine_pos_source: 'controller',
  // P2-1：抓拍落地时假后端同步补一条帧（真执行方是先写 outbox 再返回结果的），
  // 用来断言"结果落地 → 左栏摘要刷新"这根线真的通。
  extra_frames: [],
  summary_error: false,                   // 让 /api/station_summary 回"prod 查不到"
  summary_extra: {},                      // 直接往摘要里塞某站的 {last_ts,last_ok}（验角标分支）
  next_detail: null,                      // 下一条结果的 detail（抓拍那几条要看得见归属）
  cmd_seq: 0,                             // 指令序号：结果 id 必须**唯一**（见 /api/cmd 的注释）
  capture_seq: 0,                         // 抓拍序号：让每次抓拍落地的帧依次更新（见 /api/cmd）
  // 真执行方会在几百毫秒内领走并写回结果；默认照做，否则页面会一直停在"执行中"，
  // 后面的用例就全被上锁挡住了（那是假后端的锅，不是页面的）。
  auto_complete_ms: 150,
};

function completeSoon() {
  if (!state.auto_complete_ms) return;
  const cmd = state.cmd;
  const detail = state.next_detail || '已执行（假后端）';
  state.next_detail = null;
  setTimeout(() => {
    if (state.cmd !== cmd) return;
    const data = {};
    if (cmd.kind === 'goto') data.position_yz = [cmd.args.y, cmd.args.z];
    if (cmd.kind === 'home') data.position_yz = [0, 0];
    state.result = { id: cmd.id, kind: cmd.kind, args: cmd.args, data,
      ok: true, detail, ended_at: NOW };
    state.cmd = null;
    state.progress = null;                    // 结果落地，实时流收场（与执行方一致）
  }, state.auto_complete_ms);
}

// 假的"最近站位"：与 patrol.stations.nearest_station 同一口径（按格距归一化后再比），
// 格距直接取 GRID 里那两个字段（= 后端 grid_geometry 的算法），不再另算一遍。
// 抓拍不选站位时，归属由后端按当前位置判定 —— 页面只是把结果显示出来。
function fakeNearest(y, z) {
  const py = GRID.y_pitch, pz = GRID.z_pitch;
  let best = null, bestD = Infinity;
  for (const st of STATIONS) {
    const d = ((y - st.y) / py) ** 2 + ((z - st.z) / pz) ** 2;
    if (d < bestD) { bestD = d; best = st; }
  }
  return best;
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
      room: { ok: true, allowed: true, room_id: '611', entry_date: '2026-09-04', batch_no: 'BATCH-10', day: 10,
              text: '第 10 天：巡检' },
      envelope: { y: [0, 4492], z: [-212, 0] },
      events: [{ ts: NOW, level: 'info', text: '页面已连接' }],
    });
  if (path === '/api/rounds') {
    return json(200, { ok: true, rounds: ROUNDS, prod_error: null });
  }
  }
  if (path === '/api/images') {
    if (state.images_error) return json(500, { error: 'boom' });
    const params = new URL('http://console.local' + url).searchParams;
    const stationId = params.get('station_id');
    const roundId = params.get('round_id');
    const rows = roundId
      ? IMAGES.filter(r => r.round_id === roundId)
      : IMAGES.map(r => ({ ...r, station_id: stationId || r.station_id }));
    const response = json(200, { ok: true, rows,
      n_local: rows.filter(r => r.source === 'local').length,
      n_prod: rows.filter(r => r.source !== 'local').length });
    const delay = state.image_delay[stationId] || 0;
    return delay ? new Promise(resolve => setTimeout(() => resolve(response), delay)) : response;
  }
  if (path === '/api/station_summary') {
    // 与 deploy/console.py 的 station_summary 同形状：一次全量 + 取**每站末帧**。
    // prod 不可用时 ok:false + prod_error、summary 为空——页面据此不显示"暂无历史帧"。
    // 只有 last_ts/last_ok 两个字段（2026-09-21 起不再报帧数）。
    if (state.summary_error) {
      return json(200, { ok: false, prod_error: 'prod 查询失败：不通', summary: {} });
    }
    const summary = {};
    for (const r of IMAGES.concat(state.extra_frames)) {
      const cur = summary[r.station_id];
      if (!cur || r.ts > cur.last_ts) {
        summary[r.station_id] = { last_ts: r.ts, last_ok: !!r.ok };
      }
    }
    return json(200, { ok: true, prod_error: null,
                       summary: { ...summary, ...state.summary_extra } });
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
    // frames 每轮 +15：真上游是个**只增不减**的累计计数，页面用它做 fps 差值。
    // 假件要是给个常数，差值恒为 0 ⇒ fps 那一支永远测不到（顺带就让"不显示累计帧数"
    // 这条断言变成了空转）。
    state.preview_frames += 15;
    return json(200, { available: true, error: null,
                       upstream: { ok: true, frames: state.preview_frames, viewers: 1,
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
    // id 用**单调序号**，不用 state.calls.length：用例里会清空 calls 来数"这一次
    // 发了什么"，那样两条指令会撞成同一个 id，而页面按 id 去重（observeCommandResult），
    // 第二条结果被静默忽略 —— 表现是"抓拍了但摘要不刷新"，查半天才想起是假件的锅。
    state.cmd_seq += 1;
    state.cmd = { id: 'c-' + state.cmd_seq, kind: body.kind, args: body.args || {}, by: body.by,
                  session: state.session ? state.session.token : '', started_at: null };
    state.result = null;
    // 真执行方是**先写 outbox 索引、再返回结果**的（manual_exec._capture），
    // 所以帧要在 completeSoon 之前就落地——摘要刷新挂在结果落地那一刻。
    // 归属：给了 station_id 就按它；没给就按**当前位置最近的格点**（非格点抓拍走这条）。
    if (body.kind === 'capture') {
      const [py, pz] = state.machine_position;
      const sid = (body.args && body.args.station_id) || (fakeNearest(py, pz) || {}).id;
      const st = STATIONS.find(s => s.id === sid);
      const off = st ? Math.max(Math.abs(py - st.y), Math.abs(pz - st.z)) : 0;
      const auto = !(body.args && body.args.station_id);
      state.next_detail = `已抓拍 ${sid}${auto ? '（自动归到最近站位）' : ''}`
        + `（拍摄位置 Y=${py.toFixed(2)} Z=${pz.toFixed(2)}`
        + (off > 50 ? `，偏离 ${sid} 格点 ΔY=${(py - st.y).toFixed(1)} ΔZ=${(pz - st.z).toFixed(1)}` : '')
        + '）';
      if (sid) {
        // 每次抓拍给一个**更晚**的 ts：否则第二次抓拍产出的帧比第一次旧，
        // "抓拍落地 ⇒ 摘要刷新"那条断言就会被前一次的结果顶住，变成空转。
        const mm = String(5 + state.capture_seq).padStart(2, '0');
        state.capture_seq += 1;
        state.extra_frames.push({ ts: '2026-09-14T10:' + mm + ':00', station_id: sid,
                                  box_id: st ? st.box_id : null, angle_profile: 'top45', ok: true,
                                  object_name: '20260914/' + (st ? st.box_id : 'BX') + '_' + sid
                                    + '_top45_10' + mm + '00', source: 'local' });
      }
    }
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
  // 抓拍**不**在这份名单里：它不移动机构（ADR-0016 §6），后端（MOTION_KINDS 只有
  // goto/jog/home）与执行方都不校验会话。2026-09-21 现场"打开画面点抓拍没反应"
  // 就是它被误关进了这份名单——`disabled` 的按钮连 click 都不派发。
  check('未接管时点动/定位/回零都禁用（会动机构的才要会话）',
    ['#gotobtn', '#homebtn'].every(s => $(s).disabled) &&
    Array.from(document.querySelectorAll('[data-jog]')).every(b => b.disabled));
  check('未接管时抓拍**可用**（不移动机构 ⇒ 不需要会话）', $('#capbtn').disabled === false);
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
  check('有内联 favicon（P2-6：不再每次加载 404）',
    !!document.querySelector('link[rel="icon"]'), document.querySelector('link[rel="icon"]') ? '有' : '(无)');

  // P2-1：站位摘要（**末帧时间 + 末帧失败角标**）——选站前就看出哪站有问题
  check('S102 摘要行给出末帧时间',
    T('.stn[data-id="S102"] .stnsum').includes('末帧 09-14 09:30'),
    T('.stn[data-id="S102"] .stnsum'));
  check('摘要行**不报帧数**（窗口内计数不是总量，2026-09-21 去掉）',
    !/\d+\s*帧/.test(T('.stn[data-id="S102"] .stnsum')) && !/共/.test(T('.stn[data-id="S102"] .stnsum')),
    T('.stn[data-id="S102"] .stnsum'));
  check('更早那一帧失败过，但末帧是好的 ⇒ 不打角标',
    !T('.stn[data-id="S102"] .stnsum').includes('⚠'),
    T('.stn[data-id="S102"] .stnsum'));
  check('S101 没有历史帧时明说"暂无历史帧"（不是留白）',
    T('.stn[data-id="S101"] .stnsum') === '暂无历史帧', T('.stn[data-id="S101"] .stnsum'));
  check('摘要写进 aria-label（读屏拿得到，视觉小字它看不见）',
    ($('.stn[data-id="S102"]').getAttribute('aria-label') || '').includes('末帧 09-14 09:30')
      && !/共\s*\d+\s*帧/.test($('.stn[data-id="S102"]').getAttribute('aria-label') || ''),
    $('.stn[data-id="S102"]').getAttribute('aria-label'));
  check('摘要正常时 #listnote 不占位', $('#listnote').style.display === 'none');

  // 末帧失败 ⇒ 角标要出现（"要不要补拍"就看这一条）。用后端直接报 last_ok=false 来验，
  // 不去动历史那几条帧的时序（那边有一整套逐帧导航的断言挂着顺序）。
  state.summary_extra = { S101: { last_ts: '2026-09-14T13:00:00', last_ok: false } };
  document.dispatchEvent(new window.Event('visibilitychange'));
  await sleep(300);
  check('末帧失败时打"⚠ 末帧失败"角标',
    T('.stn[data-id="S101"] .stnsum').includes('⚠ 末帧失败'), T('.stn[data-id="S101"] .stnsum'));
  check('末帧失败的角标也进 aria-label',
    ($('.stn[data-id="S101"]').getAttribute('aria-label') || '').includes('末帧失败'),
    $('.stn[data-id="S101"]').getAttribute('aria-label'));
  state.summary_extra = {};
  document.dispatchEvent(new window.Event('visibilitychange'));
  await sleep(300);
  check('角标随末帧恢复而消失',
    T('.stn[data-id="S101"] .stnsum') === '暂无历史帧', T('.stn[data-id="S101"] .stnsum'));

  // 摘要取不到 ≠ 没拍过：此时**不渲染**"暂无历史帧"，只在列表下方给一句说明
  state.summary_error = true;
  document.dispatchEvent(new window.Event('visibilitychange'));   // 页面恢复可见 ⇒ 重取摘要
  await sleep(300);
  check('摘要取不到时不显示"暂无历史帧"（避免和"没拍过"混淆）',
    document.querySelectorAll('.stnsum').length === 0,
    document.querySelectorAll('.stnsum').length + ' 行还有摘要');
  check('摘要取不到时 #listnote 说明原因',
    $('#listnote').style.display !== 'none' && T('#listnote').includes('prod'), T('#listnote'));
  state.summary_error = false;
  document.dispatchEvent(new window.Event('visibilitychange'));
  await sleep(300);
  check('恢复后摘要行回来', !!$('.stn[data-id="S102"] .stnsum'));

  // P2-4：左栏可折叠（窄屏/平板让中栏全宽）
  // ⚠️ jsdom 不把样式表规则反映到 .style 上，所以这里只断言 DOM 层的 class/文案/aria，
  //    "整列真的隐藏了"由 CSS 规则 main.list-hidden>section:first-child 负责（浏览器里生效）。
  await click('#listtoggle');
  check('收起后 main 打上 list-hidden（左栏由 CSS 收起）',
    document.querySelector('main').classList.contains('list-hidden'),
    document.querySelector('main').className);
  check('折叠按钮文案与 aria-expanded 同步',
    T('#listtoggle') === '展开' && $('#listtoggle').getAttribute('aria-expanded') === 'false',
    T('#listtoggle') + ' / ' + $('#listtoggle').getAttribute('aria-expanded'));
  await click('#listtoggle');
  check('再点一次恢复', !document.querySelector('main').classList.contains('list-hidden')
    && T('#listtoggle') === '收起', T('#listtoggle'));

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

  // P1-3：导轨图拆成静态层/动态层——每秒轮询不该再把整张图重建一遍
  const mapStaticBefore = $('#map .map-static');
  await sleep(1200);                      // 至少等一拍 tick（1s）
  check('导轨图静态层不随每秒轮询重建',
    !!mapStaticBefore && $('#map .map-static') === mapStaticBefore,
    mapStaticBefore ? '同一节点 ✓' : '(缺失 .map-static)');
  check('动态层独立存在（十字/虚线每拍只更新它）', !!$('#map .map-dyn'),
    $('#map .map-dyn') ? '✓' : '(缺失 .map-dyn)');
  // 收尾：别把坐标/选中态带进后面的用例（历史模式断言"未选站位"的提示）
  $('#gtY').value = ''; $('#gtZ').value = '';
  $('#gtY').dispatchEvent(new window.Event('input'));
  mapsvg.dispatchEvent(new window.MouseEvent('mouseleave', { bubbles: true }));

  // 1b. 历史模式：无实时站位焦点时仍显示最新一轮的拓扑拼图。
  await click('#modeSeg [data-mode="history"]');
  check('历史模式：操作区让位给筛选', $('#rtcol').style.display === 'none' && $('#hcol').style.display === '');
  check('历史模式：中栏换成拼图/大图/曲线',
    $('#hmain').style.display === '' && $('#rtmain').style.display === 'none');
  check('默认选择最新巡检轮次', $('#roundSel').value === 'round-20260914', $('#roundSel').value);
  check('最新轮次仍展示完整的 12 列 × 4 层网格',
    document.querySelectorAll('#timeline .mosaic-cell').length === 48,
    document.querySelectorAll('#timeline .mosaic-cell').length + ' 格');
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

  // 3b. 抓拍不再要求先选站位（P0-5 的那句旧文案已随 2026-09-21 的改动换掉）：
  //     没选站位也能拍，页面要把"会归到哪一站"提前说清，不能拍完才知道。
  check('未选站位时抓拍下方说明归属规则', T('#capnote').includes('最近的 S101'), T('#capnote'));
  // 3c. 读屏不再被每秒刷新的读数区骚扰（P1-1）：容器无 aria-live，"当前动作"走独立节点
  check('读数区容器不带 aria-live（原先每秒播报一次）',
    !$('.readouts').hasAttribute('aria-live'), $('.readouts').getAttribute('aria-live') || '(无)');
  check('"当前动作"有独立的 sr-only 播报节点',
    !!$('#stagelive') && $('#stagelive').getAttribute('aria-live') === 'polite'
    && $('#stagelive').className.includes('sr-only'), $('#stagelive') ? $('#stagelive').className : '(缺失)');
  // 3d. 历史模式三张卡 DOM 顺序 = 视觉顺序（P1-2：原先 grid-row 重排，Tab 焦点与读屏大纲错位）
  const hmainFirst = document.querySelector('#hmain .card h2');
  check('历史模式第一张卡是"大图"（DOM 顺序即视觉顺序）',
    !!hmainFirst && hmainFirst.textContent.includes('大图'), hmainFirst ? hmainFirst.textContent : '(无)');

  // 4. 定位：页内二次确认（P2-5，取代原生 confirm()）+ 载荷
  state.calls.length = 0;
  state.machine_position = null;
  state.machine_pos_source = 'unknown';
  state.result = null;
  await sleep(1100);
  $('#gtY').value = '1200'; $('#gtZ').value = '100';
  $('#gtY').dispatchEvent(new window.Event('input'));
  await sleep(120);
  await click('#gotobtn');
  check('定位前弹出页内确认（.confirm-pop，不是原生 confirm）',
    !!document.querySelector('.confirm-pop'), document.querySelector('.confirm-pop') ? '有弹窗' : '(无弹窗)');
  check('当前位置未知时不伪造 0 mm 距离',
    T('.confirm-pop p').includes('无法计算距离') && !T('.confirm-pop p').includes('0 mm'),
    T('.confirm-pop p'));
  await sleep(50);
  document.querySelector('.confirm-pop [data-yes]').click();
  await sleep(400);
  check('确认后定位载荷 = {y:1200, z:-100}',
    JSON.stringify(lastCmd()) === '{"kind":"goto","args":{"y":1200,"z":100}}', JSON.stringify(lastCmd()));
  check('结构化位置结果更新可信读数（两位小数）',
    T('#actualpos').includes('Y=1200.00') && T('#actualpos').includes('Z=100.00') && T('#actualpos').includes('最后成功目标'),
    T('#actualpos'));
  state.machine_position = [200.0, -20.0];
  state.machine_pos_source = 'controller';
  await sleep(1100);

  state.calls.length = 0;
  await click('#gotobtn');
  check('点"移动到该点"先弹确认（不是直接发指令）', !!document.querySelector('.confirm-pop'),
    document.querySelector('.confirm-pop') ? '有弹窗' : '(无弹窗)');
  document.querySelector('.confirm-pop [data-no]').click();
  await sleep(300);
  check('取消确认后**一条都不发**', lastCmd() === null || lastCmd() === undefined,
    JSON.stringify(lastCmd()));

  // 5. 浏览器侧软限位：越界不发、当场标红
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

  // 6. 抓拍：**不选站位也能拍**（按当前位置归到最近的格点 = 非格点抓拍这条路），
  //    选了站位就按选的走。抓拍 2026-09-17 并入实时画面卡（拍的就是"此刻画面里的位置"）。
  state.machine_position = [200.0, -20.0];
  await sleep(1100);                          // 等一拍，让页面拿到这个位置
  check('未选站位时抓拍**可用**（非格点也要能拍）', $('#capbtn').disabled === false);

  // 6a. 抓拍**不需要会话**（ADR-0016 §6「手动抓拍不移动机构」；后端 MOTION_KINDS 只有
  //     goto/jog/home，执行方 `manual_exec` 里 `session` 出现 0 次）。
  //     ⚠️ 2026-09-21 现场反馈"打开画面点抓拍没反应"就是这条被漏了：页面复用了运动按钮
  //     那份 locked（含 !session_active），没接管时抓拍就 disabled；而 **disabled 的按钮
  //     连 click 都不派发** ⇒ onclick 不执行、后端也收不到 ⇒ 操作者只看到"毫无反应"。
  //     本地测不到，是因为前面的用例早就 takebtn 过、会话一直有效——**假件的默认状态比
  //     现场"更好"**。所以这里必须显式撤掉会话再测（"打开画面"这一步本来也不需要会话）。
  state.session = null;
  await sleep(1200);
  check('未接管时抓拍**仍可用**（抓拍不是运动指令 ⇒ 不要会话）',
    $('#capbtn').disabled === false, 'disabled=' + $('#capbtn').disabled);
  state.calls.length = 0;
  await click('#capbtn');
  check('未接管也能真把抓拍发出去（未选站位 ⇒ 载荷不带 station_id）',
    JSON.stringify(lastCmd()) === '{"kind":"capture","args":{}}', JSON.stringify(lastCmd()));
  await sleep(400);
  state.session = { token: 't-1', opened_at: NOW, expires_at: '2026-09-14T10:05:00' };   // 复原
  // 抓拍序号归零：这一枪已经吃掉一个序号，而后面两条断言钉的是它们自己的序号（05→06），
  // 不归零会被这一条挤走。重复的 05 不影响断言（摘要只取最大 ts）。
  state.capture_seq = 0;
  await sleep(1200);
  check('未选站位时预告会归到最近的 S101', T('#capnote').includes('最近的 S101'), T('#capnote'));

  // 「位置读不到」是**现场常态**（console 按 ADR-0004/0013 不连控制器，real_pos 只在
  // 巡检中由站位推出来）——2026-09-21 在真机上才发现：早先的写法在这条常态下整句留空，
  // 预告等于死的。所以这条断言钉的是"读不到位置时也要说清规则"。
  state.machine_position = null;
  state.machine_pos_source = 'unknown';
  await sleep(1100);
  check('位置读不到时预告仍说清规则（不留空）',
    T('#capnote').includes('按实际位置归到最近的格点') && T('#capnote').includes('写在下方结果里'),
    T('#capnote'));
  state.machine_position = [200.0, -20.0];
  state.machine_pos_source = 'controller';
  await sleep(1100);
  check('抓拍与「看画面」在同一张卡（实时画面卡）',
    !!$('#rtmain #capbtn') && $('#capbtn').closest('.card') === $('#pvbtn').closest('.card'));

  // 挪到站位表以外的位置（S102 右侧 138mm、层内偏 41mm）⇒ 归属改成 S102，且要报出偏移
  state.machine_position = [700.0, -20.0];
  await sleep(1100);
  check('位置变了 ⇒ 预告改口到最近的 S102', T('#capnote').includes('最近的 S102'), T('#capnote'));
  state.calls.length = 0;
  await click('#capbtn');
  check('未选站位时抓拍载荷**不带** station_id（归属交给后端按位置判）',
    JSON.stringify(lastCmd()) === '{"kind":"capture","args":{}}', JSON.stringify(lastCmd()));
  await sleep(400);
  check('抓拍结果说清归到哪一站、离格点多远',
    T('#cmdline').includes('自动归到最近站位') && T('#cmdline').includes('偏离 S102 格点'),
    T('#cmdline'));
  state.machine_position = [200.0, -20.0];    // 复原：后面的"控制器实读"断言依赖这个位置

  // 再走"显式指定站位"这条老路：从**地图**上点站位（S102 图心 ≈ 136,27，命中圈内）
  mapsvg.dispatchEvent(new window.MouseEvent('click', { clientX: 136, clientY: 27, bubbles: true }));
  await sleep(400);
  check('点地图上的站点 = 选中站位', T('#imgtitle').includes('S102'), T('#imgtitle'));
  check('选中后坐标框填成该站目标（两位小数）', $('#gtY').value === '561.50' && $('#gtZ').value === '21.20',
    $('#gtY').value + ' / ' + $('#gtZ').value);
  check('选中站位后抓拍仍可用', $('#capbtn').disabled === false);
  check('选中站位后不再预告归属（按选的走）', T('#capnote') === '', T('#capnote'));
  state.calls.length = 0;
  await click('#capbtn');
  check('抓拍载荷带 station_id=S102',
    JSON.stringify(lastCmd()) === '{"kind":"capture","args":{"station_id":"S102"}}', JSON.stringify(lastCmd()));
  await sleep(1500);                          // 等结果落地（~150ms）再等一拍轮询把摘要刷回来
  // P2-1：结果落地 ⇒ 左栏摘要旁路 TTL 刷新。假后端在 202 时就同步补了那一条帧
  // （真执行方先写 outbox 再返回结果），所以这里必须看得到新的末帧时间。
  // 这根线断了现场的表现是：抓拍成功了，左栏末帧时间还停在几分钟前。
  check('抓拍落地后左栏摘要刷新（末帧时间更新）',
    T('.stn[data-id="S102"] .stnsum').includes('末帧 09-14 10:06'),
    T('.stn[data-id="S102"] .stnsum'));
  check('刷新走的是 refresh=1（旁路服务端 TTL 缓存）',
    [...state.calls].some(c => c.url === '/api/station_summary?refresh=1'),
    [...state.calls].filter(c => c.url.includes('station_summary')).map(c => c.url).join(' | '));

  // 6b. 置灰时必须说出原因（P0-5）——三种真被拒的情形与后端 409 逐条对应。
  //     "点了没反应"的另一半就在这里：按钮禁用了却不说为什么，操作者只会反复点。
  state.estop = true;
  await sleep(1200);
  check('急停置位时抓拍置灰并说明"先复位急停"',
    $('#capbtn').disabled === true && T('#capnote').includes('复位急停'), T('#capnote'));
  state.estop = false;
  await sleep(1200);

  state.patrol_active = true;
  await sleep(1200);
  check('巡检中抓拍置灰并说明"机构归本轮"',
    $('#capbtn').disabled === true && T('#capnote').includes('巡检进行中')
      && T('#capnote').includes('后再拍'), T('#capnote'));
  state.patrol_active = false;
  await sleep(1200);

  // busy：让假件别自动完成，这条指令就停在"在飞"
  state.auto_complete_ms = 0;
  await click('#capbtn');
  await sleep(300);
  check('有指令在飞时抓拍置灰并说明"等它跑完"',
    $('#capbtn').disabled === true && T('#capnote').includes('上一条指令还没结束'), T('#capnote'));
  // 收场：手动放行这条指令，否则后面的用例会一直被 busy 锁住
  state.auto_complete_ms = 150;
  const inflight0 = state.cmd;
  state.cmd = null;
  state.result = { id: inflight0.id, kind: inflight0.kind, args: inflight0.args, data: {},
                   ok: true, detail: '已执行（假后端）', ended_at: NOW };
  state.progress = null;
  await sleep(1200);
  check('放行后抓拍恢复可用', $('#capbtn').disabled === false);

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

  // 12. 历史模式：按批次/轮次拼图 / 原图 / 生长曲线 / 筛选 / 与 prod 不通的区别
  await click('#modeSeg [data-mode="history"]');
  await sleep(400);
  const slots = Array.from(document.querySelectorAll('#timeline .mosaic-cell'));
  const stationCells = Array.from(document.querySelectorAll('#timeline .mosaic-cell[data-station-id]'));
  check('拼图固定为 48 个拓扑槽位', slots.length === 48, slots.length + ' 格');
  check('配置站位各占唯一 layer/col 槽位',
    stationCells.length === STATIONS.length &&
      new Set(stationCells.map(cell => cell.dataset.stationId)).size === STATIONS.length &&
      stationCells[0].getAttribute('aria-label').includes('第 1 层第 1 框') &&
      stationCells[1].getAttribute('aria-label').includes('第 1 层第 2 框') &&
      stationCells[2].getAttribute('aria-label').includes('第 4 层第 12 框'),
    stationCells.map(cell => cell.getAttribute('aria-label')).join(' | '));
  check('未配置的 45 个位置保持独立空格',
    document.querySelectorAll('#timeline .mosaic-cell.vacant').length === 45);
  check('无 round_id 的旧轮次未被猜测归组', T('#roundnote').includes('没有唯一关联 ID'), T('#roundnote'));
  check('失败帧在自己的站位格中标出',
    T('#timeline .mosaic-cell[data-station-id="S101"]').includes('采图失败'));

  const liveTargetBefore = [$('#gtY').value, $('#gtZ').value].join(',');
  await click('#timeline .mosaic-cell[data-station-id="S101"]');
  check('点击历史站位不改动实时控制目标',
    [$('#gtY').value, $('#gtZ').value].join(',') === liveTargetBefore, liveTargetBefore);
  check('点击历史站位不改变实时选站', $('.stn[data-id="S102"]').getAttribute('aria-pressed') === 'true');
  await click('#timeline .mosaic-cell[data-station-id="S102"]');
  await sleep(150);

  // 历史图的地址**必须**走 console（ADR-0003）：页面不直连 MinIO。
  // 2026-09-17 现场："历史图全是黑的"、抓拍完不显示——页面把 MinIO 的**控制网地址**
  // 当 `<img src>`，而上位机走 VPN 只到 10.77.77.x，那个地址对它是黑洞：每格等一次
  // 超时，最后留一块深色方块。看着像"相机拍黑了"，其实是一张都没取到。
  const shotSrc = Array.from(document.querySelectorAll('#timeline .mosaic-cell img'))
    .map(img => img.getAttribute('src') || '');
  // 用 pathname 比：`safeImageUrl` 会把地址规范化成绝对 URL，别去赌字符串前缀
  const shotPath = shotSrc.map(s => { try { return new URL(s, 'http://console.local').pathname; }
                                      catch (e) { return '(非法)'; } });
  check('缩略图走 /api/image（同源代理），不是 MinIO 直连地址',
    shotPath.length === 1 && shotPath.every(p => p === '/api/image'), shotSrc.join(' | ') || '(没有 img)');
  check('缩略图地址里没有控制网地址',
    !shotSrc.some(s => s.includes('192.168') || s.includes(':9000')), shotSrc.join(' | '));
  check('地址带的是对象名（端口/主机由后端拼）',
    shotSrc.some(s => s.includes(encodeURIComponent('20260914/B102_S102_top45_093000'))),
    shotSrc.join(' | '));

  // 取不到图时要说"取不到"，而不是留一块黑方块（两者现场看着一模一样）
  document.querySelectorAll('#timeline .mosaic-cell img')
    .forEach(img => img.dispatchEvent(new window.Event('error')));
  check('取不到图的格子标成 noimg（不是一块黑方块）',
    document.querySelectorAll('#timeline .mosaic-cell.noimg').length === 1 &&
      T('#timeline .mosaic-cell.noimg .mosaic-thumb').includes('图取不到'),
    document.querySelectorAll('#timeline .mosaic-cell.noimg').length + ' 格');
  check('角度档只列数据里有的（全部 + top0 + top45）',
    document.querySelectorAll('#angleSel option').length === 3,
    document.querySelectorAll('#angleSel option').length + ' 项');

  await click('#timeline .mosaic-cell[data-station-id="S102"]');
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

  // 日期与角度筛选作用于所选巡检轮次。
  $('#fromDate').value = '2026-09-15'; $('#fromDate').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('日期筛选没有轮次时明确提示', T('#timeline').includes('所选批次/日期没有'), T('#timeline'));
  await click('#clearfilter');
  await sleep(150);
  check('清空日期筛选后回到最新轮次', $('#roundSel').value === 'round-20260914', $('#roundSel').value);
  $('#angleSel').value = 'top0'; $('#angleSel').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('角度筛选隐藏其他帧并保留失败帧状态',
    T('#filterinfo').includes('筛出 1 帧') &&
      T('#timeline .mosaic-cell[data-station-id="S101"]').includes('采图失败'));
  await click('#clearfilter');
  await sleep(150);
  check('清空角度筛选后保留 48 个格位', document.querySelectorAll('#timeline .mosaic-cell').length === 48);

  $('#roundSel').value = 'round-20260913'; $('#roundSel').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('同一批次可切换到前一轮', $('#roundSel').value === 'round-20260913', $('#roundSel').value);
  check('前一輪本地原图有待同步标记', !!document.querySelector('#timeline .mosaic-cell.local'));
  check('前一轮缺帧的站位保留为空',
    T('#timeline .mosaic-cell[data-station-id="S412"]').includes('本轮无帧'));
  check('图片请求按 round_id 精确筛选',
    state.calls.some(call => call.url.includes('round_id=round-20260913')));

  $('#batchSel').value = encodeURIComponent(JSON.stringify(['611', '2026-09-04', 'BATCH-09']));
  $('#batchSel').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('批次筛选切换到该批次最新轮次', $('#roundSel').value === 'round-20260911', $('#roundSel').value);
  check('空轮次中已配置站位仍留在原槽位',
    T('#timeline .mosaic-cell[data-station-id="S102"]').includes('本轮无帧'));
  $('#batchSel').value = encodeURIComponent(JSON.stringify(['611', '2026-09-04', 'BATCH-10']));
  $('#batchSel').dispatchEvent(new window.Event('change'));
  await sleep(150);
  check('切回当前批次后默认最新轮次', $('#roundSel').value === 'round-20260914', $('#roundSel').value);

  // 拓扑导航按 layer/col 顺序经过已配置站位，不会把空白格算成帧。
  check('帧计数指出当前配置站位', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  await click('#prevframe');
  check('上一站切到 S101', T('#frameinfo').includes('第 1 / 3 站位'), T('#frameinfo'));
  check('失败帧说明"没有可用的图"（不是一块黑）', T('#viewhint').includes('没有可用的图'),
    T('#viewhint').slice(0, 40));
  await click('#latestframe');
  check('跳到最新图像所在站位', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  // 方向键也能翻帧（输入框聚焦时不抢键——真实浏览器里 keydown 的 target 是聚焦元素，
  // 所以这里要从输入框上派发，而不是从 document 上派发）
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  await sleep(150);
  check('右方向键也能翻到下一站', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  $('#fromDate').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  await sleep(150);
  check('输入框聚焦时方向键不抢（不切站）', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  await click('#latestframe');

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
  // 实时链路不保存中间帧（preview.Broadcaster 只留最后一帧），所以状态行只说
  // **帧率**与**帧龄**这两件可行动的事；累计帧数只作 fps 的差值输入，不显示。
  check('状态行给出帧率（帧率是 fps 差值算的）', T('#pvnote').includes('fps'), T('#pvnote'));
  check('状态行给出帧龄与观众数',
    T('#pvnote').includes('最新一帧') && T('#pvnote').includes('名观众'), T('#pvnote'));
  check('状态行**不显示**上游累计帧数（"累积了 N 帧"是错觉）',
    !/[0-9]+\s*帧(?!前)/.test(T('#pvnote')), T('#pvnote'));
  // P0-4：按钮文案必须跟着状态走（原版恒为"看画面"，现场把它当坏开关）
  check('画面打开后按钮变成"关闭画面"', T('#pvbtn') === '关闭画面', T('#pvbtn'));
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

  await click('#nextframe');
  check('下一站切回 S102', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  await click('#dropbtn');
  await sleep(400);
  check('放开接管后画面自动关闭',
    !$('#pvimg').getAttribute('src'), $('#pvimg').getAttribute('src') || '(已清空)');
  await click('#prevframe');
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
  check('看画面与抓拍按钮同排，错误提示不挤占按钮宽度',
    !!$('.preview-actions') && $('.preview-actions').contains($('#pvbtn')) &&
      $('.preview-actions').contains($('#capbtn')) && !$('.preview-actions').contains($('#pverr')),
    'buttons share a row; error status is outside');
  check('预览提示与位置读数在图像框外',
    !!$('.pvwrap') && !$('.pvwrap').contains($('#pvhint')) && !$('.pvwrap').contains($('#actualpos')),
    'hint/readout do not overlap the image frame');
    T('#pvstate'));
  check('关掉后按钮回到"看画面"', T('#pvbtn') === '看画面', T('#pvbtn'));
  await click('#pvbtn');
  await sleep(400);
  check('手动"看画面"又开起来', ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');
  check('再打开后按钮又是"关闭画面"', T('#pvbtn') === '关闭画面', T('#pvbtn'));

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
