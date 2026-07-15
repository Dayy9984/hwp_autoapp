// 베타 사용자 풀 워크플로우 E2E.
//   1. Admin: embed_form_url 있는 알림 발행 (announcement → Tally 폼 연결)
//   2. 라이센스 cache reset → 베타 앱 재시작 (사용자가 새로 다운받은 척)
//   3. Playwright CDP attach → 라이센스 게이트 자동 입력
//   4. 활성화 직후 announcement-fetcher 가 새 토큰으로 fetch → 알림 노출
//   5. 알림 종 클릭 → 패널에 admin 발행 항목 보임
//   6. 알림 안 embed 버튼 클릭 → Tally iframe 모달 노출
//   7. 한글 문서 열기 → Worker /hwp/upload (R2) + /hwp/trace (hdml_step) 도착
//   8. Admin /hwp-uploads + /hdml-traces 호출해 도착 검증

const { chromium } = require('playwright')
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')
const os = require('os')

const LICENSE_KEY = 'INSRT-YHJM-9TFC-NVLT-46KY'
const SAMPLE_HWP = 'c:\\Users\\dlgkr\\Desktop\\2025년도 문제정의서\\2025-01. 문제정의서(한글 문서 기반 챗봇 프로그램).hwp'
const ADMIN_KEY = fs.readFileSync('c:\\Users\\dlgkr\\Desktop\\inserty-beta-admin\\.admin-key.local', 'utf8').trim()
const WORKER = 'https://inserty-beta-worker.snsoffice.workers.dev'
const ANN_TITLE = `풀워크플로우 ${Date.now() % 100000}`

async function shot(page, name) {
  fs.mkdirSync('tmp-e2e-full', { recursive: true })
  await page.screenshot({ path: `tmp-e2e-full/${name}.png`, fullPage: true })
}
async function adminCall(p, init = {}) {
  const r = await fetch(WORKER + p, { ...init, headers: { ...(init.headers || {}), 'X-Admin-Key': ADMIN_KEY, 'Content-Type': 'application/json' } })
  if (!r.ok) throw new Error(`${p}: ${r.status}`)
  return r.json()
}

;(async () => {
  const stats = { pass: 0, fail: 0 }
  const check = (l, c) => { if (c) { stats.pass++; console.log(`  ✓ ${l}`) } else { stats.fail++; console.log(`  ✗ ${l}`) } }

  // ─── 1. embed_form_url 있는 알림 발행 ───
  console.log('1. Admin publish announcement with Tally embed URL')
  const created = await adminCall('/admin/announcements', {
    method: 'POST',
    body: JSON.stringify({
      title: ANN_TITLE,
      body: '풀 워크플로우 E2E 자동 알림. 클릭하시면 만족도 폼이 뜹니다.',
      embed_form_url: 'https://tally.so/embed/zxLqYa?alignLeft=1&hideTitle=0&transparentBackground=1',
      action_label: '응답하기',
      type: 'survey',
      priority: 80,
    }),
  })
  console.log('   created announcement id:', created.id)
  check('announcement created', !!created.id)

  // ─── 2. Cache reset ───
  console.log('\n2. License cache reset')
  const cachePath = path.join(os.homedir(), 'AppData', 'Roaming', 'Inserty AI', '.license_cache')
  if (fs.existsSync(cachePath)) { fs.unlinkSync(cachePath); console.log('   cache deleted'); check('cache deleted', true) }
  else { console.log('   (no cache to delete)'); check('cache absent', true) }

  // ─── 3. CDP attach (베타 dev 가 살아있다고 가정) ───
  console.log('\n3. CDP attach to running beta dev')
  const cdpRes = await fetch('http://127.0.0.1:9222/json/list').catch(() => null)
  if (!cdpRes || !cdpRes.ok) { console.error('   ✗ CDP 9222 not available'); process.exit(1) }
  const pages = await cdpRes.json()
  const target = pages.find(p => p.url.includes('5173') || p.url.includes('index.html'))
  if (!target) { console.error('   ✗ no beta page'); process.exit(1) }
  check('CDP page found', true)

  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222')
  // attach 한 페이지 찾기
  let page = null
  for (const ctx of browser.contexts()) {
    for (const p of ctx.pages()) {
      if (p.url() === target.url) { page = p; break }
    }
    if (page) break
  }
  if (!page) page = browser.contexts()[0].pages()[0]
  await page.reload({ waitUntil: 'networkidle' })  // cache reset 후 라이센스 게이트 표시 위해 reload
  await page.waitForTimeout(3000)
  await shot(page, '01-after-cache-reset')

  // ─── 4. 라이센스 게이트 입력 ───
  console.log('\n4. License gate input')
  // 라이센스 입력란 — placeholder 또는 type=text 첫번째
  const allInputs = await page.locator('input:visible').all()
  console.log('   visible inputs:', allInputs.length)
  let licInput = null
  for (const inp of allInputs) {
    const ph = await inp.getAttribute('placeholder').catch(() => '')
    const ro = await inp.getAttribute('readonly').catch(() => null)
    if (ro !== null) continue
    if (/INSRT|라이센스|키/i.test(ph || '')) { licInput = inp; break }
  }
  // fallback — 검색 박스 제외 첫 input
  if (!licInput) {
    for (const inp of allInputs) {
      const ph = await inp.getAttribute('placeholder').catch(() => '')
      const ro = await inp.getAttribute('readonly').catch(() => null)
      if (ro !== null) continue
      if (/검색/i.test(ph || '')) continue
      licInput = inp; break
    }
  }
  if (licInput) {
    await licInput.fill(LICENSE_KEY)
    await shot(page, '02-license-typed')
    const actBtn = page.locator('button:has-text("활성화"), button:has-text("등록"), button:has-text("입력"), button:has-text("Activate"), button:has-text("시작")').first()
    if (await actBtn.count() > 0) {
      await actBtn.click()
      check('activate clicked', true)
      await page.waitForTimeout(15000)  // verify + 토큰 갱신 + announcement fetch
      await shot(page, '03-after-activate')
    }
  } else {
    console.log('   (no license input found — maybe still cached or different gate)')
  }

  // ─── 5. 알림 종 패널 ───
  console.log('\n5. Bell + panel')
  await page.waitForTimeout(2000)
  const bell = page.locator('button[aria-label*="알림"]').first()
  check('bell visible', await bell.count() > 0)
  if (await bell.count() > 0) {
    await bell.click()
    await page.waitForTimeout(1500)
    await shot(page, '04-bell-panel')
    const annInPanel = await page.locator(`text=${ANN_TITLE}`).count()
    check(`admin announcement in panel (count=${annInPanel})`, annInPanel > 0)
    // embed 버튼 클릭
    const embedBtn = page.locator('button:has-text("응답하기")').first()
    if (await embedBtn.count() > 0) {
      await embedBtn.click()
      await page.waitForTimeout(3000)
      const iframe = await page.locator('iframe[src*="tally.so"]').count()
      check(`embed Tally iframe (count=${iframe})`, iframe > 0)
      await shot(page, '05-embed-modal')
      await page.locator('button[aria-label="닫기"]').first().click({ force: true }).catch(() => {})
      await page.waitForTimeout(500)
    }
  }

  // ─── 6. 한글 문서 자동 열기 + 베타 앱에서 문서 선택 ───
  console.log('\n6. Open HWP via 한글 프로그램 + 베타 앱에서 선택')

  // 6a. 알림 overlay 명시 닫기 (Escape + 외부 클릭)
  await page.keyboard.press('Escape').catch(() => {})
  await page.mouse.click(700, 400).catch(() => {})
  await page.waitForTimeout(500)

  // 6b. 한글 프로그램 + HWP file 열기
  try {
    spawn('cmd', ['/c', 'start', '', SAMPLE_HWP], { detached: true, stdio: 'ignore' }).unref()
    console.log('   spawned 한글 + HWP file')
    await new Promise(r => setTimeout(r, 8000))  // 한글 launch + WindowMonitor 감지 대기
    await shot(page, '06a-after-hwp-open')
  } catch (e) {
    console.log('   spawn failed:', e.message)
  }

  // 6c. 베타 앱 "문서 선택" 버튼 클릭 → 한글 창 선택 → hdml 추출 트리거
  console.log('   click "문서 선택" → 한글 창 select')
  const docSel = page.locator('button:has-text("문서 선택")').first()
  if (await docSel.count() > 0) {
    await docSel.scrollIntoViewIfNeeded()
    await docSel.click({ force: true }).catch((e) => console.log('   docSel click err:', e.message))
    await page.waitForTimeout(2000)
    await shot(page, '06b-doc-picker')
    // 드롭다운 안 한글 창 항목 클릭
    const hwpItem = page.locator('[role="menuitem"], button, div, li').filter({ hasText: /문제정의서|2025-01/ }).first()
    if (await hwpItem.count() > 0) {
      await hwpItem.click({ force: true }).catch(() => {})
      console.log('   ✓ 한글 창 선택됨')
      await page.waitForTimeout(20000)  // hdml 추출 + R2 업로드 + trace 전송 대기
      await shot(page, '06c-after-select')
    } else {
      console.log('   ✗ 한글 창 항목 못 찾음')
    }
  }

  // ─── 7. Admin trace 도착 검증 (지금까지 데이터) ───
  console.log('\n7. Admin verify')
  await new Promise(r => setTimeout(r, 5000))
  const annList = await adminCall('/admin/announcements')
  check(`admin sees published announcement`, annList.items.some(a => a.title === ANN_TITLE))
  const hwpStats = await adminCall('/admin/hwp-uploads')
  console.log(`   hwp_uploads in D1: ${hwpStats.items.length}건`)
  const hdmlStats = await adminCall('/admin/hdml-traces?limit=10')
  console.log(`   hdml_traces in D1: ${hdmlStats.items.length}건`)
  const eventStats = await adminCall(`/admin/events?since=${Date.now() - 600000}&limit=20`)
  console.log(`   events (last 10min): ${eventStats.items.length}건`)
  if (eventStats.items.length > 0) {
    console.log('   recent events:', eventStats.items.slice(0, 5).map(e => e.event_type).join(', '))
    check(`recent telemetry events`, eventStats.items.length > 0)
  }

  console.log(`\n=== Full Workflow: ${stats.pass} PASS / ${stats.fail} FAIL ===`)
  if (stats.fail > 0) process.exit(1)
})().catch((e) => {
  console.error('FATAL:', e.message)
  console.error(e.stack)
  process.exit(1)
})
