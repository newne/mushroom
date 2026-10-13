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
  { id: 'S101', box_id: 'B101', y: 187.1, z: -57.0, layer: 1, col: 1, angle_profile: 'top45',
    camera_ip: '192.168.1.238', trim_y: 0, trim_z: 0, target_y: 187.1, target_z: -57.0 },
  { id: 'S102', box_id: 'B102', y: 561.5, z: -57.0, layer: 1, col: 2, angle_profile: 'top45',
    camera_ip: '192.168.1.238', trim_y: 0, trim_z: 0, target_y: 561.5, target_z: -57.0 },
  { id: 'S412', box_id: 'B412', y: 4300.0, z: -202.0, layer: 4, col: 12, angle_profile: 'top45',
    camera_ip: '192.168.1.238', trim_y: 0, trim_z: 0, target_y: 4300.0, target_z: -202.0 },
];
// 与 patrol.stations.grid_geometry() 同形状：**y_pitch/z_pitch 不能省**——页面的
// nearestStation 按格距归一化后再比站距（见该函数注释），少了这两个字段它会退回
// "直接比毫米"，与假后端的 fakeNearest 口径就不一样了：假件更松，真分歧测不出来。
const GRID = { cols: 12, layers: 4, y_pitch: 4492.0 / 12, z_pitch: 212.0 / 4,
               y_min: 0.0, y_max: 4492.0, z_min: -212.0, z_max: 0.0 };

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
  // 第三轮（round-20260910）也有一帧，且是本地待同步：让它落进拼图，
  // 好断言"下拉直接选到最早一轮后落点仍是拼图、且这一轮确实换上了新图"。
  { round_id: 'round-20260910', ts: '2026-09-10T09:30:00', station_id: 'S102', box_id: 'B102', angle_profile: 'top0',
    ok: true, object_name: '20260910/B102_S102_top0_093000', source: 'local' },
];
const ROUNDS = [
  { round_id: 'round-20260914', ts: '2026-09-14T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: false, n_results: 48, n_failures: 1 },
  { round_id: 'round-20260913', ts: '2026-09-13T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: false, n_results: 48, n_failures: 2 },
  // 巡检失败、48 张全空：这种轮次不该进列表（2026-10-13 用户口径），也不该被"上一轮"选中
  { round_id: 'round-20260912', ts: '2026-09-12T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: false, n_results: 0, n_failures: 48 },
  // 同一批次的第三轮（2026-10-13 补 fixture）：只有两轮时，按钮与"下拉直接选"的
  // 新旧状态**恰好重合**（索引 0↔1，置灰与否都对），"下拉绕过 syncRoundOptions()"
  // 这条 bug 就测不出来。加第三轮之后，从最新(0)切到最早(2) 时索引必须从 0 变 2，
  // 否则"第 1 / 3 轮"和"上一轮置灰"两条断言都会红。
  { round_id: 'round-20260910', ts: '2026-09-10T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: true, n_results: 48, n_failures: 0 },
  { round_id: null, ts: '2026-09-12T08:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-10', ok: true, n_results: 48, n_failures: 0 },
  { round_id: 'round-20260911', ts: '2026-09-11T10:00:00', room_id: '611', entry_date: '2026-09-04',
    batch_no: 'BATCH-09', ok: true, n_results: 20, n_failures: 0 },
];
// ⚠️ 2026-10-13 修正：`/growth` 的 `points` 是 **delta 行**（`analysis/api.py:204` 返回
// `GrowthDelta.to_row()`），**没有** `mean_len_mm`/`mean_cap_mm`。这里以前写的是
// `{ts, mean_len_mm, mean_cap_mm, ...}`——照"设想的形状"造的假件，于是"曲线画出来了"
// 这条断言其实在测一个生产上不存在的载荷；真机上曲线一直是空的（页面读的字段不存在）。
// 现在假件与真契约逐字段对齐，并有契约断言盯着（见文件末尾 CONTRACT_*）。
const GROWTH = [
  { ts: '2026-09-10T09:30:00', prev_ts: null, dt_h: null,
    metric: null, unit: null, d_value: null, rate_per_h: null, band: null, band_source: null,
    d_len_mm: null, d_cap_mm: null, rate_len_mm_per_h: null, rate_cap_mm_per_h: null,
    verdict: 'no_prev', verdict_text: '没有上一个时间点',
    note: '这是该框最早的一个时间点', n_now: 6, n_prev: null },
  { ts: '2026-09-13T09:30:00', prev_ts: '2026-09-10T09:30:00', dt_h: 72.0,
    metric: 'mean_projected_d_mm', unit: 'mm', d_value: 7.7, rate_per_h: 0.1069,
    band: 0.1, band_source: 'default_uncalibrated',
    d_len_mm: null, d_cap_mm: null, rate_len_mm_per_h: null, rate_cap_mm_per_h: null,
    verdict: 'growing', verdict_text: '在长', note: '平均 +0.107 mm/h（72.0 小时内 +7.7 mm）',
    n_now: 6, n_prev: 6 },
  { ts: '2026-09-14T09:30:00', prev_ts: '2026-09-13T09:30:00', dt_h: 24.0,
    metric: 'mean_projected_d_mm', unit: 'mm', d_value: 1.8, rate_per_h: 0.075,
    band: 0.1, band_source: 'default_uncalibrated',
    d_len_mm: null, d_cap_mm: null, rate_len_mm_per_h: null, rate_cap_mm_per_h: null,
    verdict: 'stalled', verdict_text: '基本没长', note: '平均 +0.075 mm/h，落在 ±0.1 mm/h 内',
    n_now: 6, n_prev: 6 },
];

// `/api/measurements`（`measurements` 表行）：绝对量在这里。
// 与 `analysis/db.py:400` 的列一致（`n/mean_len_mm/mean_cap_mm/p10_len/p90_len/quality`
// ＋票 11 的菌簇四列）。平视单档下杆长/菌盖 NULL + `no_angle_profile` ⇒ 不可测；
// **B101 故意一行都没有** ⇒ 用来验"还没测到"（不是 0、不是零增长线）。
const MEASUREMENTS = {
  B102: [
    { ts: '2026-09-10T09:30:00', room_id: '611', box_id: 'B102', n: 6,
      mean_len_mm: null, mean_cap_mm: null, p10_len: null, p90_len: null,
      quality: 'verified+no_angle_profile',
      mean_cluster_w_mm: 58.0, mean_cluster_h_mm: 44.0,
      mean_area_mm2: 3120.0, mean_projected_d_mm: 62.5 },
    { ts: '2026-09-13T09:30:00', room_id: '611', box_id: 'B102', n: 6,
      mean_len_mm: null, mean_cap_mm: null, p10_len: null, p90_len: null,
      quality: 'verified+no_angle_profile',
      mean_cluster_w_mm: 64.0, mean_cluster_h_mm: 48.0,
      mean_area_mm2: 3980.0, mean_projected_d_mm: 70.2 },
    { ts: '2026-09-14T09:30:00', room_id: '611', box_id: 'B102', n: 6,
      mean_len_mm: null, mean_cap_mm: null, p10_len: null, p90_len: null,
      quality: 'verified+no_angle_profile',
      mean_cluster_w_mm: 66.0, mean_cluster_h_mm: 49.5,
      mean_area_mm2: 4210.0, mean_projected_d_mm: 72.0 },
  ],
};

// 环境模式用：两个小时点，补光一个是"停止"(2) 一个是"关闭"(3)。
// 分类量走色带，不走折线——对它求平均没有意义（ADR-0021）。
const ENV_ROWS = [
  { ts: '2026-10-12T14:00:00', zone: '611', temp_c: 19.6, rh_pct: 92.4, co2_ppm: 1620.0, lamp_state: 2.0,
    lamp_on_min: 20.0, lamp_on_s: 20.0, lamp_off_s: 40.0, n: 60 },
  { ts: '2026-10-12T15:00:00', zone: '611', temp_c: 19.9, rh_pct: 92.5, co2_ppm: 1639.0, lamp_state: 3.0,
    lamp_on_min: 20.0, lamp_on_s: 20.0, lamp_off_s: 40.0, n: 60 },
];
const ENV_SUMMARY = {
  zone: '611', n: 120, first_ts: '2026-10-12T14:00:00', last_ts: '2026-10-12T15:00:00',
  params: {
    temp_c: { min: 19.6, q25: 19.7, median: 19.8, q75: 19.9, max: 19.9, count: 120 },
    rh_pct: { min: 92.4, q25: 92.4, median: 92.5, q75: 92.5, max: 92.5, count: 120 },
    co2_ppm: { min: 1620.0, q25: 1630.0, median: 1630.0, q75: 1639.0, max: 1639.0, count: 120 },
  },
  lamp_states: { '2': 60, '3': 60 }, gaps: [], sources_missing: [],
  lamp: { on_min_total: 40.0, on_min_per_hour: 20.0, on_s: 20.0, off_s: 40.0,
          on_m: null, off_m: null, on_effective_s: 20.0, off_effective_s: 40.0, duty: 0.333333 },
};

// 操作日志：运行经验的原始记录（谁把哪个点改成了什么）。
const OPS_ROWS = [
  { ts: '2026-10-12T10:10:00', zone: '607', device_code: 'TD1_Q3MDHUM02',
    device_name: '607库-右加湿器', point_code: 'On', point_name: '加湿开启设定',
    value: '5', operator: '现场操作员', source: '现场', content: '加湿开启设定[5]' },
];
const OPS_SUMMARY = { n: 1, first_ts: '2026-10-12T10:10:00', last_ts: '2026-10-12T10:10:00',
  by_day: { '2026-10-12': 1 }, by_device: { '607库-右加湿器': 1 },
  by_point: { '加湿开启设定': 1 }, by_operator: { '现场操作员': 1 }, by_zone: { '607': 1 } };

// 批次对比：按断档切段，最新在前
const BATCHES = [
  { batch_no: 'mogu-107', entry_date: '2026-09-30',
    start: '2026-09-30T00:00:00', end: '2026-10-12T17:00:00', hours: 290, samples: 17279, days: 12.1,
    params: { temp_c: { mean: 19.7, min: 18.0, median: 19.8, max: 21.0 },
              rh_pct: { mean: 92.3, min: 90.0, median: 92.3, max: 94.0 },
              co2_ppm: { mean: 1800.0, min: 1500.0, median: 1780.0, max: 2200.0 } },
    lamp: { lamp_on_s: 2.0, lamp_off_s: 0.0, lamp_on_m: 0.0, lamp_off_m: 120.0, on_min_total: 5.0 } },
  { batch_no: 'mogu-103', entry_date: '2026-09-17',
    start: '2026-09-17T00:00:00', end: '2026-09-29T23:00:00', hours: 300, samples: 17800, days: 12.5,
    params: { temp_c: { mean: 20.4, min: 19.0, median: 20.4, max: 22.0 },
              rh_pct: { mean: 91.0, min: 89.0, median: 91.0, max: 93.0 },
              co2_ppm: { mean: 1650.0, min: 1400.0, median: 1640.0, max: 2000.0 } },
    lamp: { lamp_on_s: 0.0, lamp_off_s: 0.0, lamp_on_m: 5.0, lamp_off_m: 120.0, on_min_total: 700.0 } },
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
  measure_error: false,                       // 让 /api/measurements 回"prod 查不到"
  env_error: false,                           // 让 /api/environment 回"prod 查不到"
  env_empty: false,                           // 让 /api/environment 回"窗口内没有数据"
  ops_error: false,                           // 让 /api/operations 回"prod 查不到"
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

// SVG 视口像素 → mm（Y, Z）：与 web/console/index.html 的 mapScale / mapPointFromEvent 同规则。
// 存在的唯一理由：测试要点图上某个位置，而"像素 ↔ mm"随坐标框架翻转过
// （Z 的框架翻过多次，2026-10-10 定稿为"顶端原点、向上为正"）。手推像素点会静默点到别的层去，
// 所以测试一律走这个换算，不写死数字。
const MAP_FOR_TEST = { W: 920, H: 170, padL: 26, padR: 12, padT: 14, padB: 22 };
function mapMmFromSvgPx(px, py) {
  const M = MAP_FOR_TEST, g = GRID;
  const iw = M.W - M.padL - M.padR, ih = M.H - M.padT - M.padB;
  const fx = Math.min(1, Math.max(0, (px - M.padL) / iw));
  const fy = Math.min(1, Math.max(0, (py - M.padT) / ih));
  const y = g.y_min + fx * (g.y_max - g.y_min);
  // Z 向上为正：图上 y 越大 ⇒ z 越小
  const z = g.z_min + (1 - fy) * (g.z_max - g.z_min);
  return [y, z];
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
      // trigger / envelope 是**真响应里总有**的两块（console.py:415-417 直接透传）。
      // 以前假件没有它们 ⇒ 页面读 s.trigger 只走到 undefined 分支，
      // "加湿期避让"（保持/延后）那块 UI **一条断言都没有**（2026-10-13 补）。
      // 形状来自 patrol_trigger.TriggerStore.state() / hold_summary()（巡检手提供）：
      //   trigger = {job|null, pending, age_s, stale, due_at, wait_s, hold}
      //   hold    = {active:false} | {active:true, until, defer_s, reason, set_by, set_at, seconds_left}
      trigger: state.trigger || { job: null, pending: false, age_s: null, stale: false,
                                  due_at: null, wait_s: null, hold: { active: false } },
      // envelope 来自 grid_geometry()（现役 Y 0…4492、Z −212…0）。
      // 注意：页面**不消费** s.envelope，它用 /api/grid 自己算（软限位拦在浏览器侧）。
      // 两个来源都出自 grid_geometry()，所以一致；这里保留真字段是为了契约完整。
      envelope: { y: [GRID.y_min, GRID.y_max], z: [GRID.z_min, GRID.z_max] },
      events: [{ ts: NOW, level: 'info', text: '页面已连接' }],
    });
  }
  if (path === '/api/rounds') {
    return json(200, { ok: true, rounds: ROUNDS, prod_error: null });
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
  if (path === '/api/measurements') {
    // 与 deploy/console.py 的 /api/measurements 同形状：{ok, box_id, rows, n}。
    // **空 rows 是正常结果**（现役 --measure 默认关、表里还没有行）⇒ 页面必须说「还没测到」，
    // 不能画一条零增长线。
    if (state.measure_error) return json(200, { ok: false, error: 'prod 查询失败：不通', rows: [] });
    const params = new URL('http://console.local' + url).searchParams;
    const boxId = params.get('box_id') || '';
    const rows = MEASUREMENTS[boxId] || [];
    return json(200, { ok: true, box_id: boxId, rows, n: rows.length });
  }
  if (path === '/api/environment') {
    if (state.env_error) return json(200, { ok: false, error: 'prod 查询失败：不通', rows: [] });
    if (state.env_empty) return json(200, { ok: true, granularity: 'hour', rows: [] });
    return json(200, { ok: true, granularity: 'hour', rows: ENV_ROWS });
  }
  if (path === '/api/environment/summary') {
    if (state.env_error) return json(200, { ok: false, error: 'prod 查询失败：不通', params: {} });
    if (state.env_empty) {
      return json(200, { ok: true, n: 0, params: {}, lamp_states: {}, gaps: [],
                         sources_missing: ['temp_c', 'rh_pct', 'co2_ppm', 'lamp_state'] });
    }
    return json(200, Object.assign({ ok: true }, ENV_SUMMARY));
  }
  if (path === '/api/environment/batches') {
    return json(200, { ok: true, zone: '611', batches: BATCHES,
                       data_from: '2026-09-17T10:00:00', data_to: '2026-10-12T18:00:00' });
  }
  if (path === '/api/operations') {
    if (state.ops_error) return json(200, { ok: false, error: 'prod 查询失败：不通', rows: [] });
    return json(200, { ok: true, rows: OPS_ROWS, summary: OPS_SUMMARY });
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

// 换轮次 / 点格位会把目标卡 scrollIntoView 带到眼前；jsdom 没实现这个方法，
// 这里做个探针，用来断言"落点"（换了轮次是不是落在拼图上）。
const scrolls = [];
window.Element.prototype.scrollIntoView = function(){ scrolls.push(this.id || this.tagName); };
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
// 有界轮询：断言"页面最终会做到某事"，而不是"在某个墙钟窗口内做到"。
// 留一条卡死在门禁里的墙钟相位用例，比没有这条用例更坏——随机红灯会把真红灯一起淹掉。
const waitFor = async (fn, ms = 8000, step = 100) => {
  const deadline = Date.now() + ms;
  while(Date.now() < deadline){
    if(fn()) return true;
    await sleep(step);
  }
  return !!fn();
};
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

  // 1a. 坐标框架（2026-10-10 定稿，ADR-0019）：Z 的原点在**顶端**、向上为正、行程 −212…0。
  //     回零向上（找正限位 = 顶端）⇒ Z=0 落在顶端、第 1 层（最上面那层）画在最上面。
  //     “层画反了”与“机器去错层”是同一个错的两个面。
  check('平面图标明 Z 向上', html.includes('Z 向上'), 'h2=' + T('section h2'));
  check('坐标范围文案来自 /api/grid 且说明 Z 向上',
    T('#envlbl').includes('Z -212…0') && T('#envlbl').includes('向上'), T('#envlbl'));
  const dots = Array.from(document.querySelectorAll('#map circle'));
  const yOf = id => Number(dots.find(c => c.dataset.id === id)?.getAttribute('cy'));
  check('第 1 层的点画在图的上半部（Z 原点在顶端）', yOf('S101') < 100, 'cy=' + yOf('S101'));
  check('第 4 层的点画在图的下半部（离顶端原点最远）', yOf('S412') > 100, 'cy=' + yOf('S412'));
  check('点动按钮标出物理方向（Z + 是向上）',
    T('[data-jog="Z+"]').includes('上') && T('[data-jog="Z-"]').includes('下'),
    T('[data-jog="Z+"]') + ' / ' + T('[data-jog="Z-"]'));
  check('点动按钮标出物理方向（Y+ 在右、Y− 在左）',
    T('[data-jog="Y+"]').includes('右') && T('[data-jog="Y-"]').includes('左'),
    T('[data-jog="Y+"]') + ' / ' + T('[data-jog="Y-"]'));
  check('方向盘轴心不重复显示位置坐标',
    $('#jogmid').textContent.trim() === 'AXIS' && !$('#jogy') && !$('#jogz'),
    $('#jogmid').textContent.trim());
  check('方向盘轴向说明对读屏公开', $('.jogpad').getAttribute('role') === 'group' &&
    $('.jogpad').getAttribute('aria-label').includes('Z 轴'), $('.jogpad').getAttribute('aria-label'));

  // 1c. 导轨图（2026-09-17 改版）：放大移到**中栏**，滑台两轴自由移动 ⇒ 点击任意位置设目标
  check('导轨图在中栏（左栏只留站位列表）',
    !!$('#rtmain #map') && !document.querySelector('main>section:first-child #map'),
    $('#rtmain #map') ? '中栏 ✓' : '不在中栏');
  check('站点 tooltip 给的是毫米坐标（两位小数），不是 SVG 像素',
    (dots.find(c => c.dataset.id === 'S101')?.querySelector('title')?.textContent || '')
      .includes('Y=187.10 Z=-57.00 mm'),
    dots.find(c => c.dataset.id === 'S101')?.querySelector('title')?.textContent || '(无 title)');
  check('站位列表不暴露设备坐标',
    !T('#list').includes('187.10') && !T('#list').includes('-57.00'), T('#list'));
  check('站位行触控高度至少 44px', window.getComputedStyle($('.stn')).minHeight === '44px',
    window.getComputedStyle($('.stn')).minHeight);
  const stationSearchInput = $('#stationSearch');
  stationSearchInput.value = 'S102';
  stationSearchInput.dispatchEvent(new window.Event('input', {bubbles:true}));
  check('站位搜索按编号过滤', document.querySelectorAll('.stn').length === 1 &&
    $('.stn').dataset.id === 'S102', T('#list'));
  stationSearchInput.value = '';
  stationSearchInput.dispatchEvent(new window.Event('input', {bubbles:true}));
  await click('[data-station-filter="no-frame"]');
  check('无历史筛选只显示无帧站位', document.querySelectorAll('.stn').length === 1 &&
    $('.stn').dataset.id === 'S412', T('#list'));
  await click('.layer-toggle[data-layer="4"]');
  check('筛选结果层可折叠且状态同步', $('.layer-toggle[data-layer="4"]').getAttribute('aria-expanded') === 'false' &&
    $('.layer-toggle[data-layer="4"]').nextElementSibling.hidden);
  await click('.layer-toggle[data-layer="4"]');
  check('筛选结果层可重新展开', $('.layer-toggle[data-layer="4"]').getAttribute('aria-expanded') === 'true' &&
    !$('.layer-toggle[data-layer="4"]').nextElementSibling.hidden);
  await click('.layer-toggle[data-layer="4"]');
  stationSearchInput.value = 'S412';
  stationSearchInput.dispatchEvent(new window.Event('input', {bubbles:true}));
  check('筛选改词不覆盖用户手动折叠', $('.layer-toggle[data-layer="4"]').getAttribute('aria-expanded') === 'false' &&
    $('.layer-toggle[data-layer="4"]').nextElementSibling.hidden);
  stationSearchInput.value = '';
  stationSearchInput.dispatchEvent(new window.Event('input', {bubbles:true}));
  await click('[data-station-filter="all"]');
  $('#list').scrollTop = 37;
  $('.stn[data-id="S101"]').focus();
  window.drawList();
  check('列表重绘保留滚动位置与焦点', $('#list').scrollTop === 37 &&
    document.activeElement.dataset.id === 'S101', `${$('#list').scrollTop} / ${document.activeElement.dataset.id}`);
  $('.layer-toggle[data-layer="1"]').focus();
  window.drawList();
  check('列表重绘保留层按钮焦点', document.activeElement.classList.contains('layer-toggle') &&
    document.activeElement.dataset.layer === '1', document.activeElement.outerHTML);
  await click('.layer-toggle[data-layer="1"]');
  check('站位层可以折叠', $('.layer-toggle[data-layer="1"]').getAttribute('aria-expanded') === 'false' &&
    $('.layer-toggle[data-layer="1"]').nextElementSibling.hidden);
  await click('.layer-toggle[data-layer="1"]');
  check('折叠站位层可以重新展开', $('.layer-toggle[data-layer="1"]').getAttribute('aria-expanded') === 'true' &&
    !$('.layer-toggle[data-layer="1"]').nextElementSibling.hidden);
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
  check('S101 的失败末帧在摘要中明确标出',
    T('.stn[data-id="S101"] .stnsum').includes('末帧 09-14 06:30') &&
      T('.stn[data-id="S101"] .stnsum').includes('末帧失败'), T('.stn[data-id="S101"] .stnsum'));
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
  check('摘要刷新后仍反映本地真实的失败末帧',
    T('.stn[data-id="S101"] .stnsum').includes('末帧 09-14 06:30') &&
      T('.stn[data-id="S101"] .stnsum').includes('末帧失败'), T('.stn[data-id="S101"] .stnsum'));

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
  //    栏本身保持可见，CSS 只收起站位工具与列表。
  await click('#listtoggle');
  check('收起后站位内容隐藏且恢复按钮留在卡片标题',
    $('#listtoggle').closest('.card').classList.contains('station-panel-collapsed') &&
      !!$('#listtoggle').closest('.card').querySelector('#listtoggle'),
    'station panel remains reachable');
  check('收起站位内容后焦点仍在恢复按钮', document.activeElement === $('#listtoggle'));
  check('窄屏折叠规则释放固定列表高度', html.includes('station-section-collapsed{height:auto'));
  check('窄屏急停卡不覆盖单栏操作区', html.includes('@media (max-width:760px)') &&
    html.includes('.estopcard{position:static}'));
  check('折叠按钮文案与 aria-expanded 同步',
    T('#listtoggle') === '展开' && $('#listtoggle').getAttribute('aria-expanded') === 'false',
    T('#listtoggle') + ' / ' + $('#listtoggle').getAttribute('aria-expanded'));
  await click('#listtoggle');
  check('再点一次恢复', !$('#listtoggle').closest('.card').classList.contains('station-panel-collapsed')
    && T('#listtoggle') === '收起', T('#listtoggle'));

  // 点击空白处 = 填坐标（不发指令！移动永远走「移动到该点」+二次确认）；
  // jsdom 不做坐标命中，补一个 getBoundingClientRect 让换算有输入。
  const mapsvg = $('#map');
  mapsvg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 920, height: 170 });
  state.calls.length = 0;
  mapsvg.dispatchEvent(new window.MouseEvent('click', { clientX: 300, clientY: 40, bubbles: true }));
  await sleep(120);
  const blankPt = mapMmFromSvgPx(300, 40);
  check('地图空白处点击填入两位小数坐标',
    $('#gtY').value === blankPt[0].toFixed(2) && $('#gtZ').value === blankPt[1].toFixed(2),
    $('#gtY').value + ' / ' + $('#gtZ').value + '（期望 ' + blankPt.map(v=>v.toFixed(2)).join(' / ') + '）');
  check('待定目标画出了虚线框', !!document.querySelector('#map .map-pending'));
  mapsvg.dispatchEvent(new window.MouseEvent('mousemove', { clientX: 300, clientY: 40, bubbles: true }));
  check('悬停显示光标处坐标',
    T('#maphover').includes('Y=' + blankPt[0].toFixed(2)) && T('#maphover').includes('Z=' + blankPt[1].toFixed(2)),
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
  await sleep(120);
  check('历史模式：操作区让位给筛选', $('#rtcol').style.display === 'none' && $('#hcol').style.display === '');
  check('历史模式：中栏换成拼图/大图/曲线',
    $('#hmain').style.display === '' && $('#rtmain').style.display === 'none');
  check('默认选择最新巡检轮次', $('#roundSel').value === 'round-20260914', $('#roundSel').value);
  check('最新轮次仍展示完整的 12 列 × 4 层网格',
    document.querySelectorAll('#timeline .mosaic-cell').length === 48,
    document.querySelectorAll('#timeline .mosaic-cell').length + ' 格');
  check('未选站位时不画曲线', T('#curve').includes('选一个站位'), T('#curve'));
  // 1b-2. 拼图格子尺寸跟着容器走（2026-10-13 补）：原来只在 renderHistory() 里算一次，
  //       窗口缩放或从环境模式切回来之后格子保持旧尺寸（窄屏被撑出横向滚、宽屏浪费一栏）。
  //       jsdom 没有布局（clientWidth 恒为 0），所以这里把容器宽度**钉死**再触发 resize，
  //       断言的是"重算这条链还在"，不是像素绝对值（像素仍按 clientWidth 算，见 cellSizeFor）。
  {
    const tlEl = $('#timeline');
    const mosaicEl = () => document.querySelector('#timeline .mosaic');
    const styleNow = () => (mosaicEl() ? mosaicEl().style : null);
    const widthOf = style => {
      const m = style && /repeat\([^,]+,\s*(\d+)px\)/.exec(style.gridTemplateColumns || '');
      return m ? Number(m[1]) : 0;
    };
    const before = widthOf(styleNow());

    Object.defineProperty(tlEl, 'clientWidth', { value: 900, configurable: true });
    Object.defineProperty(tlEl, 'clientHeight', { value: 500, configurable: true });
    const observerCtor = window.ResizeObserver;
    window.ResizeObserver = undefined;          // 不能真构造：jsdom 没有布局，observer 不回调
    window.dispatchEvent(new window.Event('resize'));
    await sleep(120);                            // rAF 是 16ms 节拍，给足一拍
    const wide = widthOf(styleNow());
    // 900px 栏位 → (900−20−62−11×2)/12 ≈ 66px。别写死 156：156 是**上限**，
    // 要 ~1974px 栏位才够到；写死上限等于把"页面按容器算"错测成"页面按上限算"。
    check('窗口变宽后拼图格子按新容器宽度重算（900px 栏位 → 约 66px，不再是 64px 兜底）',
      wide !== before && wide >= 60 && wide <= 72, 'before=' + before + ' after=' + wide);

    Object.defineProperty(tlEl, 'clientWidth', { value: 4000, configurable: true });
    window.dispatchEvent(new window.Event('resize'));
    await sleep(120);
    check('极宽栏位置上限 156px（再大就得横向滚，反而看不全一屏）',
      widthOf(styleNow()) === 156, String(widthOf(styleNow())));

    Object.defineProperty(tlEl, 'clientWidth', { value: 560, configurable: true });
    window.dispatchEvent(new window.Event('resize'));
    await sleep(120);
    const narrow = widthOf(styleNow());
    check('窗口变窄后格子跟着收（560px 栏位 → 保底 56px），且网格列数与格数不变',
      narrow === 56 && narrow !== wide
      && (mosaicEl().style.gridTemplateColumns || '').includes('repeat(12,56px)')
      && document.querySelectorAll('#timeline .mosaic-cell').length === 48,
      'wide=' + wide + ' narrow=' + narrow + ' / ' + document.querySelectorAll('#timeline .mosaic-cell').length + ' 格');

    // 让后面"选择格位"的用例不受这个钉死的宽度影响
    delete tlEl.clientWidth; delete tlEl.clientHeight;
    window.ResizeObserver = observerCtor;
  }
  // 1b-3. "换轮次后第一眼是拼图"要写成结构断言（2026-10-13）：
  //       现在是靠 switchRound()→scrollToMosaic() 的副作用 + 注释保证的，一旦有人把详情区
  //       挪出拼图卡，或往拼图卡前面插一张卡，这条口径就静默破掉。
  {
    const card = $('#mosaiccard');
    const firstCard = document.querySelector('#hmain > .card');
    check('拼图卡仍是历史模式第一张卡（换轮次的落点没被别的卡挤走）',
      !!card && card === firstCard, (firstCard && (firstCard.id || firstCard.tagName)) || '(无)');
    check('完整图像区在拼图卡**内部**（大图置顶之后口径仍成立：滚到卡顶 = 拼图在视口内）',
      !!card && !!$('#detailpane') && card.contains($('#detailpane')),
      card && $('#detailpane') ? (card.contains($('#detailpane')) ? '在卡内' : '跑到卡外了') : '(缺元素)');
  }
  await click('#modeSeg [data-mode="realtime"]');
  check('切回实时模式：操作区回来', $('#rtcol').style.display === '' && $('#rtmain').style.display === '');

// 1c. 环境模式：实测参数为主线；同样不占相机（与历史模式一样的让位规矩）
await click('#modeSeg [data-mode="env"]');
await sleep(250);
check('环境模式：中栏换成实测环境、右栏换成统计',
      $('#envmain').style.display === '' && $('#rtmain').style.display === 'none'
      && $('#envcol').style.display === '' && $('#rtcol').style.display === 'none',
      [$('#envmain').style.display, $('#rtmain').style.display].join(' / '));
// 用 '/api/environment?' 精确匹配，别把 /api/environment/batches 也捞进来
const envCall = state.calls.filter(c => c.url.includes('/api/environment?')).pop();
check('环境模式：按小时粒度取数、带上库房号',
      !!envCall && envCall.url.includes('granularity=hour') && envCall.url.includes('zone=611'),
      envCall ? envCall.url : '(没有发请求)');
check('环境模式：三条实测曲线（温/湿/CO₂ 各一根轴；补光不再画状态色带）',
      !!$('#envchart svg') && document.querySelectorAll('#envchart svg path').length === 3
      && document.querySelectorAll('#envchart svg rect').length === 0,
      document.querySelectorAll('#envchart svg path').length + ' path / '
      + document.querySelectorAll('#envchart svg rect').length + ' rect');
check('环境模式：补光色带已去掉，改为卡片里的四个实际参数',
      T('#envlight').includes('OnSSet') && T('#envlight').includes('OffMSet'),
      T('#envlight').slice(0, 50));
check('环境模式：结论先行——KPI 磁贴给出温度/湿度/CO₂ 的中位与区间',
      T('#envkpi').includes('温度') && T('#envkpi').includes('19.8')
      && T('#envkpi').includes('92.5') && T('#envkpi').includes('采样'), T('#envkpi').slice(0, 120));
check('环境模式：曲线高度收窄（车道 76 → 54，三个量仍各占一根轴）',
      (() => { const svg = document.querySelector('#envchart svg');
               return !!svg && svg.getAttribute('viewBox') === '0 0 920 204'; })(),
      (document.querySelector('#envchart svg') || {}).getAttribute
        ? document.querySelector('#envchart svg').getAttribute('viewBox') : '(无)');
check('环境模式：统计面板一行一个参数（中位放大 + 区间 + IQR 条），不再是一张 6 列表',
      T('#envsummary').includes('19.8') && T('#envsummary').includes('120')
      && document.querySelectorAll('#envsummary .qrow').length === 3
      && document.querySelectorAll('#envsummary .qbar i').length === 3,
      document.querySelectorAll('#envsummary .qrow').length + ' 行 / '
      + document.querySelectorAll('#envsummary .qbar i').length + ' 条');
check('环境模式：补光状态显示成语义（2=停止、3=关闭），不显示裸数字',
      T('#envlight').includes('停止') && T('#envlight').includes('关闭'), T('#envlight'));
check('环境模式：补光给出折算周期与点亮分钟（并标它是推算，不是照度）',
      T('#envlight').includes('开 20 秒') && T('#envlight').includes('占空比 33.3')
      && T('#envlight').includes('折算点亮'), T('#envlight'));
check('环境模式：补光卡片标出四个原值，数不对时能一眼看出是哪一侧',
      T('#envlight').includes('开启秒设定') && T('#envlight').includes('关闭分设定'),
      T('#envlight').slice(0, 60));
check('环境模式：操作记录列出"谁把哪个点改成了什么"',
      T('#envops').includes('607库-右加湿器') && T('#envops').includes('加湿开启设定')
      && T('#envops').includes('5'), T('#envops').slice(0, 60));
check('环境模式：批次对比按台账批次号列出当前与前一批的实际参数',
      T('#envbatches').includes('mogu-107') && T('#envbatches').includes('（当前）')
      && T('#envbatches').includes('mogu-103') && T('#envbatches').includes('19.7'),
      T('#envbatches').slice(0, 90));
check('环境模式：批次对比说明数据覆盖起点（更早的批次只有窗口没有数据）',
      T('#batchnote').includes('环境数据自 2026-09-17'), T('#batchnote').slice(0, 60));
check('环境模式：批次对比显示各批次的补光周期（秒档与分档都认）',
      T('#envbatches').includes('开 2 秒') && T('#envbatches').includes('停 120 分'),
      T('#envbatches').slice(-60));
check('环境模式：没有区间表就直说，不编一个"最优"出来',
      T('#batchnote').includes('05 号票') && T('#batchnote').includes('ranges'), T('#batchnote').slice(0, 60));
check('环境模式：左栏站位表让位（单列看数据）',
      document.querySelector('main').classList.contains('envmode')
      && window.getComputedStyle(document.querySelector('main>section:first-child')).display === 'none',
      document.querySelector('main').className);
check('环境模式：操作记录给出按天计数与操作人',
      T('#opsstat').includes('10-12 1 次') && T('#opsstat').includes('现场操作员'), T('#opsstat'));
await click('#modeSeg [data-mode="realtime"]');
await sleep(200);
check('切回实时模式：环境区让位', $('#envmain').style.display === 'none' && $('#rtmain').style.display === '');

// 「这段时间没人操作」与「问不到 prod」必须分得开——前者是有用的结论，后者要排障
state.ops_error = true;
await click('#modeSeg [data-mode="env"]');
await sleep(250);
check('环境模式：操作记录取不到时如实说，不拿"没人操作"顶替',
      T('#envops').includes('取不到') && T('#opsstat').includes('prod'),
      T('#envops') + ' / ' + T('#opsstat'));
state.ops_error = false;
await click('#modeSeg [data-mode="realtime"]');
await sleep(150);



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
  await click('[data-jog="Z+"]');
  check('Z+ 点动载荷 = {axis:Z, mm:+5}',
    JSON.stringify(lastCmd()) === '{"kind":"jog","args":{"axis":"Z","mm":5}}', JSON.stringify(lastCmd()));
  await click('[data-jog="Z-"]');
  check('Z− 点动载荷 = {axis:Z, mm:−5}',
    JSON.stringify(lastCmd()) === '{"kind":"jog","args":{"axis":"Z","mm":-5}}', JSON.stringify(lastCmd()));
  state.calls.length = 0;
  document.dispatchEvent(new window.KeyboardEvent('keydown',
    {key:'ArrowUp', bubbles:true, cancelable:true}));
  await sleep(50);
  document.dispatchEvent(new window.KeyboardEvent('keyup', {key:'ArrowUp', bubbles:true}));
  await sleep(1200);
  check('键盘↑ = Z+（物理向上）',
    JSON.stringify(lastCmd()) === '{"kind":"jog","args":{"axis":"Z","mm":5}}', JSON.stringify(lastCmd()));
  state.calls.length = 0;
  document.dispatchEvent(new window.KeyboardEvent('keydown',
    {key:'ArrowUp', bubbles:true, cancelable:true}));
  await sleep(50);
  const repeatedJogKey = new window.KeyboardEvent('keydown',
    {key:'ArrowUp', bubbles:true, cancelable:true, repeat:true});
  document.dispatchEvent(repeatedJogKey);
  const jogRequestsDuringRepeat = state.calls.filter(call =>
    call.method === 'POST' && call.url === '/api/cmd' && call.body?.kind === 'jog').length;
  check('接管后指令在飞时方向键重复不会滚动页面',
    repeatedJogKey.defaultPrevented && jogRequestsDuringRepeat === 1,
    `defaultPrevented=${repeatedJogKey.defaultPrevented}; jogs=${jogRequestsDuringRepeat}`);
  document.dispatchEvent(new window.KeyboardEvent('keyup', {key:'ArrowUp', bubbles:true}));
  await sleep(1200);
  state.calls.length = 0;
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
  // 2026-10-13 用户口径：换轮次的落点是"整体生长情况"，所以拼图在前、大图在后；
  // DOM 顺序仍然等于视觉顺序（这条没变）。
  check('历史模式第一张卡是"巡检拼图"（换轮次先看整体，DOM 顺序即视觉顺序）',
    !!hmainFirst && hmainFirst.textContent.includes('巡检拼图'), hmainFirst ? hmainFirst.textContent : '(无)');

  // 4. 定位：页内二次确认（P2-5，取代原生 confirm()）+ 载荷
  state.calls.length = 0;
  state.machine_position = null;
  state.machine_pos_source = 'unknown';
  state.result = null;
  await sleep(1100);
  $('#gtY').value = '1200'; $('#gtZ').value = '-100';
  $('#gtY').dispatchEvent(new window.Event('input'));
  await click('#gotobtn');
  check('定位前弹出页内确认（.confirm-pop，不是原生 confirm）',
    !!document.querySelector('.confirm-pop'), document.querySelector('.confirm-pop') ? '有弹窗' : '(无弹窗)');
  check('确认弹窗期间点动按钮上锁', $('[data-jog="Z+"]').disabled);
  document.dispatchEvent(new window.KeyboardEvent('keydown', {key:'Tab', bubbles:true, cancelable:true}));
  check('确认弹窗 Tab 焦点留在对话框内', document.activeElement === $('.confirm-pop [data-no]'));
  document.dispatchEvent(new window.KeyboardEvent('keydown', {key:'Tab', shiftKey:true, bubbles:true, cancelable:true}));
  check('确认弹窗 Shift+Tab 焦点环可反向切换', document.activeElement === $('.confirm-pop [data-yes]'));
  state.calls.length = 0;
  document.dispatchEvent(new window.KeyboardEvent('keydown', {key:'ArrowUp', bubbles:true, cancelable:true}));
  await sleep(50);
  check('确认弹窗期间方向键不能触发点动',
    !state.calls.some(call => call.method === 'POST' && call.url.startsWith('/api/cmd') && call.body?.kind === 'jog'),
    JSON.stringify(state.calls.filter(call => call.method === 'POST')));
  check('当前位置未知时不伪造 0 mm 距离',
    T('.confirm-pop p').includes('无法计算距离') && !T('.confirm-pop p').includes('0 mm'),
    T('.confirm-pop p'));
  await sleep(50);
  document.querySelector('.confirm-pop [data-yes]').click();
  await sleep(400);
  check('确认后定位载荷 = {y:1200, z:-100}',
    JSON.stringify(lastCmd()) === '{"kind":"goto","args":{"y":1200,"z":-100}}', JSON.stringify(lastCmd()));
  check('结构化位置结果更新可信读数（两位小数）',
    T('#actualpos').includes('Y=1200.00') && T('#actualpos').includes('Z=-100.00') && T('#actualpos').includes('最后成功目标'),
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

  // 再走"显式指定站位"这条老路：从**地图**上点站位。
  // ⚠️ 点圆心的 cx/cy，不写死像素：Z 的框架翻过（2026-10-10 定稿顶端原点），
  // 写死的坐标会静默点到别的层——旧版这里写的 136,27 现在就属于第 4 层。
  const s102dot = Array.from(document.querySelectorAll('#map circle'))
    .find(c => c.dataset && c.dataset.id === 'S102');
  const s102x = Number(s102dot.getAttribute('cx')), s102y = Number(s102dot.getAttribute('cy'));
  mapsvg.dispatchEvent(new window.MouseEvent('click', { clientX: s102x, clientY: s102y, bubbles: true }));
  await sleep(400);
  check('点地图上的站点 = 选中站位', T('#imgtitle').includes('S102'), T('#imgtitle'));
  check('站位选择状态可被辅助技术识别', $('.stn[data-id="S102"]').getAttribute('aria-pressed') === 'true' &&
    $('.stn[data-id="S102"]').getAttribute('aria-current') === 'false',
    $('.stn[data-id="S102"]').getAttribute('aria-label'));
  check('选中后坐标框填成该站目标（两位小数）', $('#gtY').value === '561.50' && $('#gtZ').value === '-57.00',
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
  state.cmd = { id: 'c-2', kind: 'goto', args: { y: 1200, z: -100 }, started_at: NOW };
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
  // 2026-10-13 用户：缩略图下面不挂位置和时间——格子就是那张图。
  // 只在"图在、状态不正常"（采图失败/待同步）时才留一行小字，所以这里排掉那两类。
  const plainCells = Array.from(document.querySelectorAll('#timeline .mosaic-cell img'))
    .map(img => img.closest('.mosaic-cell'))
    .filter(cell => !cell.classList.contains('fail') && !cell.classList.contains('local'));
  check('缩略图下面不再挂位置与时间（正常帧一个字都没有）',
    plainCells.length > 0 && plainCells.every(cell => cell.textContent.trim() === '')
    && document.querySelectorAll('#timeline .mosaic-cell .mosaic-meta b').length === 0,
    plainCells.length + ' 个正常帧，带字的 '
    + plainCells.filter(cell => cell.textContent.trim()).length
    + '，站名节点 ' + document.querySelectorAll('#timeline .mosaic-cell .mosaic-meta b').length);
  check('状态不正常时仍有那行小字（采图失败/待同步要看得出来）',
    Array.from(document.querySelectorAll('#timeline .mosaic-cell.fail, #timeline .mosaic-cell.local'))
      .some(cell => cell.textContent.trim() !== ''),
    Array.from(document.querySelectorAll('#timeline .mosaic-cell.fail, #timeline .mosaic-cell.local'))
      .map(cell => cell.textContent.trim()).join('|') || '(无)');
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

  // 曲线：绝对量来自 /api/measurements（量），判词/速率来自 /api/growth（派生）。
  // 画的是**一条线**（最高优先可用长势量）；不再画"平均长度/平均伞盖"两条——
  // 那两个量在平视单档下不可测，而且 `/growth` 里本来就没有这两列（2026-10-13 修正）。
  check('生长曲线画出来了（一条线 + 点，量纲随数据选）',
    !!$('#curve svg') && document.querySelectorAll('#curve svg path').length === 1,
    document.querySelectorAll('#curve svg path').length + ' 条');
  check('曲线画的是绝对量且带量纲（投影径 mm，不是裸数字）',
    T('#curve').includes('投影径') && T('#curve').includes('mm'), T('#curve').slice(0, 90));
  check('曲线下方列出最近的测量值（含判词）',
    T('#growthnote').includes('70.2') && T('#growthnote').includes('在长'),
    T('#growthnote').slice(0, 90));
  // 平视单档：杆长/菌盖直径 NULL + no_angle_profile ⇒ 不可测，不是 0、不是空格
  check('杆长/菌盖直径在表里标「不可测（无角度档）」，不写 0 也不留空',
    T('#growthnote').includes('不可测（无角度档）')
    && !/\b0\s*mm/.test(T('#growthnote')), T('#growthnote').slice(0, 120));
  check('曲线区明说这两列不画曲线（换角之前）',
    T('#growthnote').includes('不画这两条曲线'), T('#growthnote').slice(0, 140));
  check('速率带量纲显示（"每天" + 单位），不是裸数字',
    T('#curve').includes('/天') && /[+-]?\d+(\.\d+)? mm\/天/.test(T('#curve')),
    T('#curve').slice(0, 160));
  check('band_source 未定标只做弱提示（不是告警色、不是硬结论）',
    T('#curve').includes('阈值未定标'), T('#curve').slice(0, 160));
  check('曲线标题带框号', T('#curvetitle').includes('B102'), T('#curvetitle'));

  // 「还没测到」必须是**空态**，不是 0、不是零增长线（巡检手 2026-10-13 特意提醒）。
  // 假件里 B101 一行测量都没有（真实现役 `--measure` 默认关、表里确实还没有行）。
  await click('#timeline .mosaic-cell[data-station-id="S101"]');
  await sleep(300);
  check('这个框一行测量都没有时明说「还没测到」',
    T('#curve').includes('还没测到'), T('#curve'));
  check('没有测量数据时**不画零增长线**、不出现 0 值（空态 ≠ 0）',
    !document.querySelector('#curve svg') && !/\b0(\.0+)?\s*(mm|mm²)/.test(T('#curve')),
    T('#curve') + ' / svg=' + !!document.querySelector('#curve svg'));
  check('空态说清为什么空（测量接线默认关、阈值未定标）',
    T('#growthnote').includes('--measure') && T('#growthnote').includes('还没测到'),
    T('#growthnote').slice(0, 120));
  await click('#timeline .mosaic-cell[data-station-id="S102"]');
  await sleep(300);

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
  check('前一轮本地原图有待同步标记', !!document.querySelector('#timeline .mosaic-cell.local'));
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

  // ---- 轮次切换（2026-10-13）：按钮在拼图卡上，空轮次不进列表也不占步 ----
  check('巡检失败、全是空的轮次不进轮次列表',
    !$('#roundSel').innerHTML.includes('round-20260912'), $('#roundSel').innerHTML.slice(0, 90));
  // 注意别读成页头的 #roundinfo——那是实时模式的"第 N/M 站"（曾经撞过 id）
  check('轮次导航说明跳过了几个空轮次',
    T('#roundnavinfo').includes('已跳过 1 个空轮次'), T('#roundnavinfo'));
  check('轮次导航给出"第几轮 / 共几轮"',
    T('#roundnavinfo').includes('第 1 / 3 轮'), T('#roundnavinfo'));
  check('轮次信息不会写进页头（页头那个是实时模式的"第 N/M 站"）',
    !T('#roundinfo').includes('轮'), T('#roundinfo') || '(空)');
  check('历史模式：站位（按层）那栏让位，中栏吃满',
    document.querySelector('main').classList.contains('histmode'),
    document.querySelector('main').className);
  check('最新一轮时"下一轮"置灰', $('#nextround').disabled === true, String($('#nextround').disabled));
  scrolls.length = 0;
  await click('#prevround');
  await sleep(300);
  check('点"上一轮"切到更早一轮（跳过空轮次，直接到 09-13）',
    $('#roundSel').value === 'round-20260913', $('#roundSel').value);
  check('换轮次的落点是拼图（自动滚到拼图卡）',
    scrolls.includes('mosaiccard'), JSON.stringify(scrolls));
  check('换轮次后不沿用上一轮选中的站位（先看整体）',
    T('#frameinfo').includes('未选'), T('#frameinfo'));
  check('中间一轮时两侧按钮都可用（不是停在最新一轮的置灰状态）',
    $('#prevround').disabled === false && $('#nextround').disabled === false,
    'prev=' + $('#prevround').disabled + ' next=' + $('#nextround').disabled);
  scrolls.length = 0;
  await click('#timeline .mosaic-cell[data-station-id="S102"]');
  await sleep(250);
  // 完整图像在拼图**上面**（同一张卡里的 detailpane），不再单开"大图"卡、也不用滚
  check('点拼图格位 → 上面的图像区显示完整图像（不跳卡、不滚动）',
    !!$('#bigimg').getAttribute('src') && !!$('#detailpane').contains($('#bigimg'))
    && scrolls.length === 0,
    ($('#bigimg').getAttribute('src') || '(无)') + ' / 滚动 ' + JSON.stringify(scrolls));
  check('图像区标出这是哪个位置',
    T('#dpstation').includes('S102'), T('#dpstation'));
  // 2026-10-13 用户：大图放预览图上面
  check('完整图像区在拼图**之前**（DOM 顺序即视觉顺序）',
    !!(document.querySelector('#mosaiccard #detailpane')
       && document.querySelector('#mosaiccard #timeline')
       && (document.querySelector('#mosaiccard #detailpane').compareDocumentPosition(
             document.querySelector('#mosaiccard #timeline')) & window.Node.DOCUMENT_POSITION_FOLLOWING)),
    'detailpane 在 timeline ' + (document.querySelector('#mosaiccard #detailpane')
      .compareDocumentPosition(document.querySelector('#mosaiccard #timeline'))
      & window.Node.DOCUMENT_POSITION_FOLLOWING ? '之前' : '之后'));
  check('图像区给足高度（clamp 到 62vh，不是一条缝）',
    /clamp\(260px,\s*62vh,\s*720px\)/.test(html),
    (html.match(/clamp\([^)]*62vh[^)]*\)/) || ['(CSS 里没找到)'])[0]);
  check('历史模式里不再有单独的"大图"卡',
    !document.querySelector('#hmain #bigcard'), 'bigcard 存在');
  await click('#nextround');
  await sleep(300);
  check('点"下一轮"回到最新一轮', $('#roundSel').value === 'round-20260914', $('#roundSel').value);

  // ---- 同一站位按天对比（2026-10-13，Chief 定的抽样维度：按天，不按轮）----
  // 一轮≈7min40s、一天约 8 轮：取"最近 6 轮"只覆盖不到 20 小时，相邻帧几乎一样，看不出生长。
  // 假件里 S102 有三天的帧（09-14 / 09-13 / 09-10），正好验证"每天一块、跨天并排"。
  await click('#timeline .mosaic-cell[data-station-id="S102"]');
  await waitFor(() => document.querySelectorAll('#cmpstrip [data-cmp]').length > 0, 4000);
  const cmpSel = () => document.querySelectorAll('#cmpstrip [data-cmp]');
  const cmpDate = el => String(((el.querySelector('.cmp-when') || {}).textContent) || '').slice(0, 10);
  check('对比视图给"当前选中站位"（标题带站位与框号）',
    T('#cmptitle').includes('S102') && T('#cmptitle').includes('B102'), T('#cmptitle'));
  check('按天抽样：三天各一块，不是"最近 3 轮"堆在同一天',
    cmpSel().length === 3
    && cmpDate(cmpSel()[0]).includes('2026-09-14')
    && cmpDate(cmpSel()[1]).includes('2026-09-13')
    && cmpDate(cmpSel()[2]).includes('2026-09-10'),
    Array.from(cmpSel()).map(cmpDate).join(' | '));
  check('对比卡如实标注"这是原图"（缩略图服务到位前不假装省带宽）',
    T('#cmphint').includes('原图'), T('#cmphint'));
  // 叠加：绝对量（来自 /api/measurements）+ 判词/速率（来自 /api/growth 的 delta 行）
  check('对比卡每天叠上绝对量且带量纲（投影径 72.0 mm）',
    T('#cmpstrip').includes('投影径') && T('#cmpstrip').includes('72 mm'), T('#cmpstrip').slice(0, 140));
  check('对比卡叠的判词带速率、速率带量纲（不是裸数字）',
    /[+-]\d+(\.\d+)? mm\/天/.test(T('#cmpstrip')), T('#cmpstrip').slice(0, 180));
  check('band_source 未定标在对比卡上只做弱提示（cmp-note，不是 bad）',
    T('#cmpstrip').includes('阈值未定标')
    && !document.querySelector('#cmpstrip .cmp-note.bad'), T('#cmpstrip').slice(0, 200));

  // 按天抽样必须真的"按时段挑"：把取样时段改到 23:20（假件里没有那一轮），
  // 断言三块都掉了"邻近时段"的标记——只堆轮次不挑时段的实现过不了这条。
  $('#cmpTime').value = '1400';
  $('#cmpTime').dispatchEvent(new window.Event('change'));
  await waitFor(() => document.querySelectorAll('#cmpstrip .cmp-note').length >= 3, 4000);
  check('改取样时段真的重挑（23:20 时三点都标"邻近时段"，不假装准）',
    document.querySelectorAll('#cmpstrip .cmp-note').length >= 3, T('#cmpstrip'));
  // 回到默认档：**必须选一个真实存在的选项值**。直接 set value='570' 会被浏览器忽略
  // （570 不在选项里：只有步长整点 + 数据里真实存在的时段），随后 dispatch change 是空动作
  // ——第一版就这么写的，结果"回到默认"那步其实没执行，白白红了两条。
  $('#cmpTime').value = 'auto';
  $('#cmpTime').dispatchEvent(new window.Event('change'));
  await waitFor(() => document.querySelectorAll('#cmpstrip .cmp-note').length === 0, 8000, 200);
  check('回到默认档（跟随当前轮次）后标记消失、说明恢复',
    T('#cmpnote').includes('按"最近 → 更早"排列'), T('#cmpnote') + ' / ' + T('#cmphint'));
  check('默认档是"跟随当前轮次"，不是一个写死的时刻',
    ($('#cmpTime').value === 'auto') && T('#cmpTime').includes('跟随当前轮次'),
    $('#cmpTime').value + ' / ' + T('#cmpTime').slice(0, 40));

  const cmpSrcs = Array.from(document.querySelectorAll('#cmpstrip img'))
    .map(img => img.getAttribute('src') || '');
  const cmpPaths = cmpSrcs.map(s => { try { return new URL(s, 'http://console.local').pathname; }
                                      catch (e) { return '(非法)'; } });
  check('对比条的图全部走 /api/image 同源代理（页面不直连 MinIO）',
    cmpSrcs.length >= 2 && cmpPaths.every(p => p === '/api/image'), cmpSrcs.join(' | ') || '(没有 img)');
  check('对比条的图带的是有效对象名（不是 cloud_url、没有控制网地址）',
    cmpSrcs.some(s => s.includes(encodeURIComponent('20260910/B102_S102_top0_093000')))
    && !cmpSrcs.some(s => s.includes('192.168') || s.includes(':9000')), cmpSrcs.join(' | '));

  // 对比条必须与拼图/原图用同一个筛选口径：筛某个角度档时，不能漏出别的档的帧。
  // ⚠️ 断言里**不能随手写一个档名**：`#angleSel` 只列当前数据里真实存在的档，且会被
  //    syncAngleOptions() 重建；写一个当时不存在的档名，赋值会被忽略、change 成了空动作，
  //    于是一条"永远通过/永远失败"的假用例（2026-07-13 联调踩到：top0 当时根本不在选项里）。
  //    所以先读回真实档名，再对它断言。
  const cmpTiles = () => Array.from(document.querySelectorAll('#cmpstrip [data-cmp]'));
  const shotAngle = 'top45';   // 当前选中轮次里 S102 那一帧的档
  check('角度筛选里有可用于对比的档（先确认断言前提成立）',
    Array.from($('#angleSel').options).some(o => o.value === shotAngle),
    Array.from($('#angleSel').options).map(o => o.value).join(','));
  document.querySelectorAll('#cmpmore').forEach(b => b.remove());
  $('#angleSel').value = shotAngle;
  $('#angleSel').dispatchEvent(new window.Event('change'));
  // 等"筛过之后的那一次渲染"跑完再断言：change 只是触发者，重绘在逐天取数回来之后。
  // ⚠️ 这里只断言"图"，别再要求"此刻一定有条"——等条件满足的那一拍条可能还没重绘完，
  //    把"条数 > 0"塞进断言就是自造竞态（连跑 5 次会稳红的那条）。
  await waitFor(() => (T('#cmpstrip').includes('该角度档无帧')
                       && !!document.querySelector('#cmpstrip img')), 8000, 150);
  // 正面断言，别写反：此刻**能有**图，但每一张都必须是所选档；被筛掉的那几天要挂原因占位。
  // （第一版写成"不许有 img"，等于把正确行为判红——"断言写了"和"断言有用"的区别就在这。）
  check('角度档筛选时，图只出现在所选档的那一天（别的天挂"该角度档无帧"）',
    cmpTiles().some(el => el.querySelector('img'))
    && cmpTiles().filter(el => el.querySelector('img'))
        .every(el => (el.dataset.url || '').includes(shotAngle))
    && cmpTiles().filter(el => !el.querySelector('img'))
        .every(el => el.textContent.includes('该角度档无帧')),
    T('#cmpstrip').slice(0, 120));
  check('被筛掉的块如实标明原因（不是默默空白）',
    T('#cmpstrip').includes('该角度档无帧'), T('#cmphint'));
  $('#angleSel').value = '';
  $('#angleSel').dispatchEvent(new window.Event('change'));
  await waitFor(() => !!document.querySelector('#cmpstrip img'), 8000, 200);

  // 点对比条里的某一天 = 看那一天的完整图像（详情区在上面，不跳卡不滚动）
  const oldest = cmpTiles().pop();
  check('对比条里能拿到最早那天的块（最新在前）',
    !!oldest && decodeURIComponent(oldest.dataset.cmp) === 'round-20260910',
    oldest ? oldest.dataset.cmp : '(无)');
  oldest.click();
  await sleep(400);
  check('点对比条里的一天 → 详情大图换成那一天的图',
    ($('#bigimg').getAttribute('src') || '').includes('20260910'), $('#bigimg').getAttribute('src') || '(无)');
  check('点过的对比块标成选中（不是"总是最新那块"高亮）',
    !!document.querySelector('#cmpstrip [data-cmp].sel')
    && T('#cmpstrip [data-cmp].sel').includes('2026-09-10'),
    document.querySelector('#cmpstrip [data-cmp].sel')
      ? T('#cmpstrip [data-cmp].sel').slice(0, 24) : '(无选中)');
  check('详情大图仍然走 /api/image（对比视图没把它换成别的源）',
    (function(){ try{ return new URL($('#bigimg').getAttribute('src'), 'http://console.local').pathname === '/api/image'; }
                 catch(e){ return false; } })(), $('#bigimg').getAttribute('src') || '(无)');

  // ---- 下拉直接选轮次必须和按钮走同一条路（2026-10-13 修）----
  // 原来 #roundSel.onchange 直接 loadRoundImages()，绕过 syncRoundOptions()：
  // 导航状态与按钮置灰停在上一轮算出的值，也不清上一轮的选中站位。
  // 先在最新一轮里选一个站位，再"下拉跳到最早一轮"——两条路行为必须一致。
  await click('#timeline .mosaic-cell[data-station-id="S101"]');
  check('下拉切轮次前：最新一轮的站位已选中（下面用它验"下拉不会沿用"）',
    T('#dpstation').includes('S101'), T('#dpstation'));
  $('#roundSel').value = 'round-20260910';
  $('#roundSel').dispatchEvent(new window.Event('change'));
  await sleep(400);
  check('下拉直接选最早一轮：选中值与轮次导航都对（不是停在"第 1 / 3 轮"）',
    $('#roundSel').value === 'round-20260910'
    && T('#roundnavinfo').includes('第 3 / 3 轮'), $('#roundSel').value + ' / ' + T('#roundnavinfo'));
  check('下拉换轮次也不沿用上一轮的站位（与按钮同一条路）',
    T('#frameinfo').includes('未选'), T('#frameinfo'));
  check('下拉落到最早一轮：最早端置灰、最新端可用',
    $('#prevround').disabled === true && $('#nextround').disabled === false,
    'prev=' + $('#prevround').disabled + ' next=' + $('#nextround').disabled);
  check('下拉换轮次真的换了源（请求按新 round_id 发）',
    state.calls.some(call => call.url.includes('round_id=round-20260910')));
  check('下拉切到最早一轮后落点仍是拼图卡（不是详情区）',
    scrolls.includes('mosaiccard'), JSON.stringify(scrolls));
  $('#roundSel').value = 'round-20260914';
  $('#roundSel').dispatchEvent(new window.Event('change'));
  await sleep(400);
  check('下拉切回最新一轮：导航状态与置灰同步复位',
    $('#roundSel').value === 'round-20260914'
    && T('#roundnavinfo').includes('第 1 / 3 轮')
    && $('#nextround').disabled === true && $('#prevround').disabled === false,
    T('#roundnavinfo') + ' / next=' + $('#nextround').disabled);
  // 换轮次会清掉选中站位（先看整体），下面那条"帧计数"用例要有选中站点，这里补回来
  await click('#latestframe');
  await sleep(150);

  // 拓扑导航按 layer/col 顺序经过已配置站位，不会把空白格算成帧。
  check('帧计数指出当前配置站位', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  await click('#prevframe');
  check('上一站切到 S101', T('#frameinfo').includes('第 1 / 3 站位'), T('#frameinfo'));
  check('失败帧说明"没有可用的图"（不是一块黑）', T('#viewhint').includes('没有可用的图'),
    T('#viewhint').slice(0, 40));
  await click('#nextframe');
  check('下一站切回 S102', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  await click('#latestframe');
  check('跳到最新图像所在站位', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  // 方向键也能翻帧（输入框聚焦时不抢键——真实浏览器里 keydown 的 target 是聚焦元素，
  // 所以这里要从输入框上派发，而不是从 document 上派发）
  await click('#prevframe');
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  await sleep(150);
  check('右方向键也能翻到下一站', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  $('#fromDate').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  await sleep(150);
  check('输入框聚焦时方向键不抢（不切站）', T('#frameinfo').includes('第 2 / 3 站位'), T('#frameinfo'));
  await click('#latestframe');

  // 两条数据通路各有各的失败形态（2026-10-13 拆开之后）：
  //   /api/measurements 挂了 ⇒ 没有绝对量 ⇒ "查不到测量数据"（要排障）；
  //   /api/growth 挂了 ⇒ 绝对量照旧，但判词/速率**必须明说取不到**，不能静默省略。
  state.growth_error = true;
  await click('#reloadbtn');
  await sleep(300);
  check('/growth 挂了：绝对量照常画，但明说判词/速率取不到（不静默）',
    !!document.querySelector('#curve svg') && T('#curve').includes('判词/速率取不到')
    && T('#curve').includes('prod 查询失败'), T('#curve').slice(-160));
  state.growth_error = false;
  state.measure_error = true;
  await click('#reloadbtn');
  await sleep(300);
  check('/measurements 挂了：如实报错（不是画一条空曲线）',
    T('#curve').includes('查不到测量数据') && T('#growthnote').includes('prod'), T('#growthnote'));
  state.measure_error = false;
  await click('#reloadbtn');
  await sleep(300);

  // 加湿保持 / 偏置（`/api/status` 的 trigger）：真形状，2026-10-13 补。
  // 这块 UI 现场用来**避开雾气拍图**（设保持 → 期间来的触发一律延后），以前假件没这个字段，
  // 只测到 undefined 分支。现在逐条钉住页面是怎么消费它的。
  state.trigger = { job: null, pending: false, age_s: null, stale: false, due_at: null,
                    wait_s: null, hold: { active: false } };
  await sleep(1300);
  check('未设保持：标签写"未设保持"、解除置灰、延后按钮可点',
    T('#holdstate') === '未设保持' && $('#holdclear').disabled === true
    && $('#deferbtn').disabled === false && T('#deferbtn').includes('延后 20 分钟'),
    [T('#holdstate'), $('#holdclear').disabled, T('#deferbtn')].join(' | '));
  // 关键：`until` 故意给"已经过去"的时刻，`seconds_left` 给 1500。
  // 页面若自己按 until 算就会显示 0/负数——这条断言就钉住"用载荷给的剩余秒数"。
  state.trigger = { job: null, pending: false, age_s: null, stale: false, due_at: null, wait_s: null,
                    hold: { active: true, until: '2026-09-14T09:00:00', defer_s: 1200,
                            reason: '加湿', set_by: 'operator', set_at: '2026-09-14T09:30:00',
                            seconds_left: 1500 } };
  await sleep(1300);
  check('保持中：标签给出剩余分钟、解除可用',
    T('#holdstate').includes('加湿保持中') && T('#holdstate').includes('25 分钟')
    && $('#holdclear').disabled === false, T('#holdstate') + ' / clear=' + $('#holdclear').disabled);
  check('剩余时间用**载荷里的 seconds_left**，不是页面自己按 until 算（until 已过期仍显示 25 分钟）',
    T('#holdstate').includes('25 分钟'), T('#holdstate'));
  // 已在排队的请求被偏置：延后按钮变成倒计时并置灰（单飞，不排队）
  state.trigger = { job: { id: 'r-1', created_at: '2026-09-14T09:59:00', created_by: 'scheduler',
                           reason: '加湿期', consumed_at: null, note: null,
                           not_before: '2026-09-14T10:20:00', pending: true },
                    pending: true, age_s: 60, stale: false, due_at: '2026-09-14T10:20:00',
                    wait_s: 900, hold: { active: false } };
  await sleep(1300);
  check('已偏置的排队请求：延后按钮显示剩余并置灰（不让人重复点）',
    T('#deferbtn').includes('已延后') && T('#deferbtn').includes('15 分钟')
    && $('#deferbtn').disabled === true, T('#deferbtn') + ' / disabled=' + $('#deferbtn').disabled);
  state.trigger = { job: null, pending: false, age_s: null, stale: false, due_at: null,
                    wait_s: null, hold: { active: false } };
  await sleep(1300);
  check('恢复未设保持后按钮态复位',
    T('#holdstate') === '未设保持' && $('#deferbtn').disabled === false, T('#deferbtn'));

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
  check('看画面与抓拍按钮同排，错误提示不挤占按钮宽度',
    !!$('.preview-actions') && $('.preview-actions').contains($('#pvbtn')) &&
      $('.preview-actions').contains($('#capbtn')) && !$('.preview-actions').contains($('#pverr')),
    'buttons share a row; error status is outside');
  check('预览提示与位置读数在图像框外',
    !!$('.pvwrap') && !$('.pvwrap').contains($('#pvhint')) && !$('.pvwrap').contains($('#actualpos')),
    'hint/readout do not overlap the image frame');

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

  // 流"卡住"（连接还在、没有新帧）浏览器不会报错：页面必须靠上游的帧龄自己发现并重连。
  // ⚠️ 页面判据是 `age > 3 && Date.now() - pv.startedAt > 4000`（index.html:2077）：
  //    **流开始后 4 秒内的窗口正常不触发重连**。原来这里固定 sleep(2600) 就断言，
  //    等于拿"这段墙钟刚好够"当判据——前面用例多插几条就会翻（2026-10-13 实测到一次）。
  //    改法：显式等过那个 4 秒窗口，再用**有界轮询**等页面做到（不是把等待调大）。
  const stalledSrc = $('#pvimg').getAttribute('src');
  await sleep(4200);                           // 显式跨过"流开始后 4 秒"的保护窗
  state.preview_frame_age = 9;
  const reconnected = await waitFor(
    () => $('#pvimg').getAttribute('src') !== stalledSrc || T('#pvstate').includes('重连'), 8000, 200);
  check('画面卡住时自动重连（换一个 src）', reconnected,
    T('#pvstate') + ' / ' + ($('#pvimg').getAttribute('src') || '(无)'));
  state.preview_frame_age = 0.4;
  await waitFor(() => T('#pvstate').includes('播放中'), 8000, 200);
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
  check('关掉后按钮回到"看画面"', T('#pvbtn') === '看画面', T('#pvbtn'));
  await click('#pvbtn');
  await sleep(400);
  check('手动"看画面"又开起来', ($('#pvimg').getAttribute('src') || '').startsWith('/api/preview'),
    $('#pvimg').getAttribute('src') || '(无)');
  check('再打开后按钮又是"关闭画面"', T('#pvbtn') === '关闭画面', T('#pvbtn'));

  // ---- 13b. 视觉/可读性护栏：能扫出来的就别靠眼睛（2026-10-13 视觉批次）----
  // 这些不是审美判断，是**以前真的踩过**的坑，写成扫描断言才不会复发：
  //   ① P0-1 的暗色主题硬编码（#2a3340/#8b98a5/#58a6ff/#7ee2a8）贴在浅色界面上像补丁
  //      —— 2026-10-13 又在导轨图里抓到一处 `#8b98a5`（Z 锚点标签）；
  //   ② `var(--x)` 用了却没定义 ⇒ 该属性静默失效，最难查的一类；
  //   ③ CSS 里出现 <10px 的字：现场远看/亮光下没有意义；
  //   ④ 键盘焦点不可见（现场有人全程用键盘走点动/急停）；
  //   ⑤ 触控目标小于 44px。
  const styleCss = (() => {
    const m = html.match(/<style>([\s\S]*?)<\/style>/);
    return m ? m[1] : '';
  })();
  // 查硬编码颜色时要去掉注释：注释里**允许**提到旧色值（那是在记录历史），
  // 顺手把整行 `//` / `*` 开头的注释行也排除掉。
  const codeOnly = html.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').filter(l => !/^\s*(\/\/|\*)/.test(l)).join('\n');
  // 定义可以出现在 CSS 里，也可以是 JS/内联样式在运行时注入的（`--cellw` 就是拼图格 JS 写的），
  // 所以定义扫描要覆盖整页代码而不仅是 <style>。
  const defined = new Set([...codeOnly.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map(m => m[1]));
  // `var(--x, fallback)` 是有意为之的兜底（CSS 允许），只把"既没定义、又没兜底"的当问题。
  const varUses = [...codeOnly.matchAll(/var\(\s*(--[a-z0-9-]+)\s*(,)?/gi)]
    .map(m => ({ name: m[1], hasFallback: !!m[2] }));
  const brokenVars = [...new Set(varUses.filter(u => !u.hasFallback && !defined.has(u.name))
    .map(u => u.name))];
  const fallbackOnly = [...new Set(varUses.filter(u => u.hasFallback && !defined.has(u.name))
    .map(u => u.name))];
  check('用到的每个 var(--x) 要么有定义、要么自带兜底（静默失效最难查）',
    brokenVars.length === 0,
    brokenVars.length ? brokenVars.join(',') : ('ok（带兜底的：' + (fallbackOnly.join(',') || '无') + '）'));
  const darkLeftovers = ['#2a3340', '#8b98a5', '#58a6ff', '#7ee2a8']
    .filter(c => codeOnly.toLowerCase().includes(c));
  check('没有暗色主题遗留硬编码（P0-1 名单；注释里的历史记载不算）',
    darkLeftovers.length === 0, darkLeftovers.join(',') || 'ok');
  const tinyFonts = [...styleCss.matchAll(/font-size:\s*([0-9.]+)px/gi)]
    .map(m => Number(m[1])).filter(n => n < 10);
  check('CSS 里没有小于 10px 的字（现场可读性下限，现下限 10.5px）',
    tinyFonts.length === 0, tinyFonts.join(',') || 'ok');
  check('触控目标下限仍在（button 至少 44px）',
    /min-height:\s*44px/.test(styleCss), (styleCss.match(/min-height:\s*\d+px/) || ['(无)'])[0]);
  check('键盘焦点环用 :focus-visible（鼠标点完不残留一圈）且用主题 accent 色',
    /:focus-visible/.test(styleCss) && /outline:\s*2px solid var\(--accent\)/.test(styleCss),
    'focus-visible=' + /:focus-visible/.test(styleCss));
  check('主题变量只有一处 :root 定义（防"定义两遍各自漂"）',
    (styleCss.match(/:root\{/g) || []).length === 1,
    String((styleCss.match(/:root\{/g) || []).length));

  // ---- 14. 契约断言：假后端的字段集必须与**真后端**的响应形状一致（2026-10-13 加）----
  // 为什么非有不可：页面读 `p.mean_len_mm / p.mean_cap_mm` 读了很久，而 `/growth` 从来
  // 没有这两列（它给的是 delta 行）——真机上曲线一直是空的、回归却一直绿，因为
  // **fixture 照着"设想的形状"写**。断言看着对、其实在测别的东西，是回归里最贵的坑。
  // 这里把三件事钉住：
  //   (1) fixture 字段 ⊆ 真契约字段 —— **不许凭空造字段**（造出来的字段页面读得到、生产读不到）；
  //   (2) 页面消费的字段 ⊆ 真契约字段 —— 页面不许读载荷里不存在的东西；
  //   (3) "页面必需"的真契约字段 ⊆ fixture —— fixture 不许漏。
  // 真契约出处逐条写在下面；**改后端形状时先改这里**，否则回归会红而不是静默变空。
  const CONTRACT = {
    // analysis/api.py:204 → growth.GrowthDelta.to_row()（growth.py:159-180）
    '/api/growth': { q: '?box_id=B102', pick: 'points',
      fields: ['box_id', 'ts', 'prev_ts', 'dt_h', 'd_len_mm', 'd_cap_mm', 'rate_len_mm_per_h',
               'rate_cap_mm_per_h', 'metric', 'unit', 'd_value', 'rate_per_h', 'band',
               'band_source', 'verdict', 'verdict_text', 'note', 'n_now', 'n_prev'],
      required: ['ts', 'metric', 'unit', 'd_value', 'rate_per_h', 'band', 'band_source',
                 'verdict', 'verdict_text'] },
    // analysis/api.py:155 → analysis/db.py:38-57（measurements 表列）
    '/api/measurements': { q: '?box_id=B102', pick: 'rows',
      fields: ['ts', 'room_id', 'box_id', 'station_id', 'angle_profile', 'n', 'mean_len_mm',
               'mean_cap_mm', 'mean_cluster_w_mm', 'mean_cluster_h_mm', 'mean_area_mm2',
               'mean_projected_d_mm', 'p10_len', 'p90_len', 'quality'],
      required: ['ts', 'quality', 'mean_len_mm', 'mean_cap_mm', 'mean_projected_d_mm',
                 'mean_area_mm2', 'n'] },
    // deploy/console.py api_rounds → analysis/db.py:59-71（rounds 表列）
    '/api/rounds': { q: '?limit=2000', pick: 'rounds',
      fields: ['ts', 'round_id', 'ok', 'n_results', 'n_failures', 'aborted', 'room_id',
               'entry_date', 'batch_no'],
      required: ['round_id', 'ts', 'ok', 'n_results', 'n_failures', 'room_id', 'entry_date',
                 'batch_no'] },
    // deploy/console.py api_environment → analysis/environment.py:28-32 FIELDS（小时聚合）
    '/api/environment': { q: '?zone=611&granularity=hour', pick: 'rows',
      fields: ['ts', 'zone', 'temp_c', 'temp_min', 'temp_max', 'rh_pct', 'rh_min', 'rh_max',
               'co2_ppm', 'co2_min', 'co2_max', 'lamp_state', 'lamp_on_s', 'lamp_off_s',
               'lamp_on_m', 'lamp_off_m', 'lamp_on_min', 'n'],
      required: ['ts', 'zone', 'temp_c', 'rh_pct', 'co2_ppm', 'lamp_state', 'n'] },
    // spec.md:341 /api/images（console 合并 local+prod，剥掉 cloud_url）
    '/api/images': { q: '?round_id=round-20260914&limit=2000', pick: 'rows',
      fields: ['ts', 'round_id', 'station_id', 'box_id', 'angle_profile', 'camera_ip', 'y', 'z',
               'object_name', 'ok', 'error', 'room_id', 'entry_date', 'batch_no', 'source'],
      required: ['ts', 'station_id', 'box_id', 'angle_profile', 'object_name', 'ok', 'source'] },
    // deploy/console.py compute_status（`:399-419`）：trigger/envelope 是**总有**的两块
    '/api/status': { q: '', pick: 'top', fields: ['ts', 'machine', 'patrol', 'room', 'trigger',
                                                   'envelope', 'events'],
      required: ['ts', 'machine', 'patrol', 'room', 'trigger', 'envelope', 'events'] },
  };
  // 页面实际消费的字段（改页面时同步改这里；这是"页面不许读不存在的字段"的锚）
  const PAGE_CONSUMES = {
    '/api/growth': ['ts', 'metric', 'unit', 'd_value', 'rate_per_h', 'band_source',
                    'verdict', 'verdict_text'],
    '/api/measurements': ['ts', 'quality', 'mean_len_mm', 'mean_cap_mm', 'mean_projected_d_mm',
                          'mean_area_mm2', 'n'],
    '/api/rounds': ['round_id', 'ts', 'ok', 'n_results', 'n_failures', 'room_id', 'entry_date',
                    'batch_no'],
    '/api/environment': ['ts', 'zone', 'temp_c', 'rh_pct', 'co2_ppm', 'lamp_state', 'lamp_on_min',
                         'lamp_on_s', 'lamp_off_s', 'n'],
    '/api/images': ['ts', 'round_id', 'station_id', 'box_id', 'angle_profile', 'object_name',
                    'ok', 'source'],
    '/api/status': ['ts', 'machine', 'patrol', 'room', 'trigger', 'events'],
  };
  for (const [path, c] of Object.entries(CONTRACT)) {
    const payload = await (await route('GET', path + c.q)).json();
    const rows = c.pick === 'top' ? [payload] : (payload[c.pick] || []);
    const keys = new Set();
    for (const r of rows) for (const k of Object.keys(r)) keys.add(k);
    const fieldSet = new Set(c.fields);
    const invented = [...keys].filter(k => !fieldSet.has(k));
    const missing = c.required.filter(k => !keys.has(k));
    const pageBad = (PAGE_CONSUMES[path] || []).filter(k => !fieldSet.has(k));
    check(`契约 ${path}：fixture 字段全部来自真契约（不许凭空造字段）`,
      invented.length === 0, invented.join(',') || 'ok');
    check(`契约 ${path}：页面消费的字段都在真契约里`,
      pageBad.length === 0, pageBad.join(',') || 'ok');
    check(`契约 ${path}：fixture 含页面必需的字段`,
      missing.length === 0, missing.join(',') || 'ok');
  }
  // 同一个 bug 的直接负向钉子：页面不许再把测量列当 `/growth` 的 delta 字段读
  // （上一版就是 `p.mean_len_mm`，而 delta 行里根本没有这一列）。
  check('页面不再把 mean_len_mm/mean_cap_mm 当 /growth 的字段读',
    !/p\.mean_(len|cap)_mm/.test(html), (html.match(/p\.mean_(len|cap)_mm[^\n]*/) || ['ok'])[0]);

  // ---- 嵌套形状：/api/status 的 trigger / hold / envelope（2026-10-13 补）----
  // 顶层键相等还不够：这块 UI 的语义全在嵌套里（保持剩余秒数、排队等待秒数）。
  // 真形状由巡检手提供（patrol_trigger.TriggerStore.state() / hold_summary()）。
  const stPayload = await (await route('GET', '/api/status')).json();
  const trigShape = stPayload.trigger || {};
  check('契约 /api/status.trigger：7 个键齐全（job/pending/age_s/stale/due_at/wait_s/hold）',
    ['job', 'pending', 'age_s', 'stale', 'due_at', 'wait_s', 'hold']
      .every(k => k in trigShape), Object.keys(trigShape).join(','));
  check('契约 /api/status.trigger.hold：无保持时只有 active',
    'active' in (trigShape.hold || {}), Object.keys(trigShape.hold || {}).join(','));
  check('契约 /api/status.envelope：{y:[min,max], z:[min,max]} 两轴各两个数（来自 grid_geometry）',
    !!stPayload.envelope && Array.isArray(stPayload.envelope.y)
    && stPayload.envelope.y.length === 2 && Array.isArray(stPayload.envelope.z)
    && stPayload.envelope.z.length === 2
    && stPayload.envelope.y.concat(stPayload.envelope.z).every(n => typeof n === 'number'),
    JSON.stringify(stPayload.envelope || null));
  state.trigger = { job: null, pending: false, age_s: null, stale: false, due_at: null,
                    wait_s: null, hold: { active: true, until: '2026-09-14T10:30:00',
                                          defer_s: 1200, reason: '加湿', set_by: 'operator',
                                          set_at: '2026-09-14T10:00:00', seconds_left: 1500 } };
  const holdActive = ((await (await route('GET', '/api/status')).json()).trigger || {}).hold || {};
  state.trigger = null;
  check('契约 /api/status.trigger.hold（有保持）：active + until/defer_s/reason/set_by/set_at/seconds_left 都在',
    ['active', 'until', 'defer_s', 'reason', 'set_by', 'set_at', 'seconds_left']
      .every(k => k in holdActive), Object.keys(holdActive).join(','));

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
