// Isolated browser regression: only synthetic data; no bank or application server.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const server = createServer(async (request,response) => {
  const name = request.url === '/' ? 'index.html' : request.url.replace('/static/','');
  if (!/^(assets\/)?[a-zA-Z0-9_-]+\.(html|js|css|svg)$/.test(name)) {response.writeHead(404).end();return;}
  try {
    const body = await readFile(new URL('../frontend-dist/'+name,import.meta.url));
    response.writeHead(200,{'Content-Type':name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.svg')?'image/svg+xml':'text/html'}).end(body);
  } catch {response.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser = await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
try {
  const page = await browser.newPage({timezoneId:'Europe/Tallinn'});
  await page.addInitScript(() => {
    if (sessionStorage.getItem('real-events')) return;
    window.EventSource = class extends EventTarget {
      constructor() { super(); window.bankEvents = this; }
      close() {}
    };
  });
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  const accounts = [{id:1,name:'Everyday',iban:'EE000000000000001234',currency:'EUR',balance:'9999999999999999.99',monitored:true},{id:2,name:'Travel',currency:'XXX',balance:'10.25',monitored:true}];
  const categories = [{id:1,name:'Food',icon:'🛒',budget:200,spent:30,earned:0,enabled:true},{id:2,name:'Salary',icon:'💰',budget:-3000,spent:0,earned:2500,enabled:true}];
  const now = new Date(), month = now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');
  const transactions = [{id:2,account_id:1,description:'Salary',amount:2500,amount_exact:'2500.00',currency:'EUR',direction:'CRDT',category_id:2,splits:[],date:month+'-02',date_kind:'booking'},{id:1,account_id:1,counterparty_name:'Transport Company',description:'<img src=x onerror=alert(1)> '+ 'Long bank description '.repeat(9),amount:30,amount_exact:'30.00',currency:'EUR',direction:'DBIT',category_id:null,splits:[],date:month+'-01',date_kind:'transaction'}];
  let failBalance = false, failStatistics = false, failMonitor = false, failCategory = false, emptyMonth = false, expireEvents = false;
  let lastCategoryPatch;
  let categoryPatchCount = 0;
  let failCategoryReload = false;
  const syncBalances = [];
  const writes = [];
  let syncCount = 0;
  let lastSuccessAt = null, failSync = false;
  const planItems = [];
  let nextPlanItemId = 1, failPlanWrite = false, failPlanReload = false;
  const planSnapshot = selectedMonth => ({
    month: selectedMonth, is_current_month: selectedMonth === month,
    totals: [{currency:'EUR',balance:accounts[0].balance,expected_income:'500.00',expected_expense:'170.00',closing_balance:selectedMonth === month ? '10000000000000329.99' : null}],
    categories: categories.map(row => ({...row,budget:String(row.budget),currency:row.currency || 'EUR',forecast_mode:row.forecast_mode || 'budget',payment_status:row.payment_status || 'auto',actual:String(row.budget < 0 ? row.earned : row.spent),remaining:row.enabled ? '170.00' : '0.00'})),
    items: planItems.filter(row => row.month === selectedMonth).map(row => ({...row,actual:'0.00',remaining:row.payment_status === 'closed' ? '0.00' : row.amount})),
  });
  await page.route('**/api/**',async route=>{
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() !== 'GET') writes.push(path);
    let result = {};
    if (path === '/api/events') {await route.fulfill({contentType:'text/event-stream',body:expireEvents?'event: expired\ndata: {}\n\n':': ready\n\n'});return;}
    if (path === '/api/accounts') result = accounts;
    else if (path === '/api/plan') {
      if (failPlanReload) {failPlanReload=false;await route.fulfill({status:500,json:{detail:'Plan refresh failed'}});return;}
      result = planSnapshot(url.searchParams.get('month'));
    }
    else if (path === '/api/plan/items' && request.method() === 'POST') {
      if (failPlanWrite) {await route.fulfill({status:500,json:{detail:'Plan save failed'}});return;}
      result = {payment_status:'auto',transaction_ids:[],...request.postDataJSON(),id:nextPlanItemId++};
      planItems.push(result);
    }
    else if (/^\/api\/plan\/items\/\d+$/.test(path)) {
      if (failPlanWrite) {await route.fulfill({status:500,json:{detail:'Plan save failed'}});return;}
      const index = planItems.findIndex(row => row.id === Number(path.split('/').at(-1)));
      if (request.method() === 'DELETE') result = {id:planItems.splice(index,1)[0].id,deleted:true};
      else result = Object.assign(planItems[index],request.postDataJSON());
    }
    else if (path === '/api/sync' && request.method() === 'GET') result = {last_success_at:lastSuccessAt};
    else if (path === '/api/sync' && request.method() === 'POST') {
      if (failSync) {await route.fulfill({status:502,json:{detail:'Bank unavailable'}});return;}
      syncCount++;
      lastSuccessAt = new Date(Date.UTC(2026,10,9,12,syncCount)).toISOString();
      const balance = syncBalances.shift();
      if (balance !== undefined) accounts[0].balance = balance;
      result = {accounts: 1, transactions_imported: balance === undefined ? 0 : 1};
    }
    else if (path === '/api/categories' && request.method() === 'GET') {
      if (failCategoryReload) {failCategoryReload=false;await route.fulfill({status:500,json:{detail:'reload failed'}});return;}
      result = categories;
    }
    else if (path === '/api/transactions') result = emptyMonth ? [] : transactions;
    else if (path.endsWith('/balance')) {
      if(failBalance){await route.fulfill({status:500,json:{detail:'test failure'}});return;}
      accounts[0].balance = request.postDataJSON().balance;result={balance:accounts[0].balance};
    } else if (path.endsWith('/currency')) {
      const account=accounts.find(item=>item.id===Number(path.split('/')[3]));
      account.currency=request.postDataJSON().currency;result={id:account.id,currency:account.currency};
    } else if (path.endsWith('/statistics')) {
      if (failStatistics) {await route.fulfill({status:500,json:{detail:'test failure'}});return;}
      const row=transactions.find(item=>item.id===Number(path.split('/')[3]));
      row.excluded_from_statistics=request.postDataJSON().excluded_from_statistics;
    } else if (path.endsWith('/monitor')) {
      if (failMonitor) {await route.fulfill({status:500,json:{detail:'test failure'}});return;}
      accounts[0].monitored=url.searchParams.get('monitored')==='true';
    }
    else if (path.endsWith('/category')) {
      const row=transactions.find(item=>item.id===Number(path.split('/')[3]));
      row.category_id=request.postDataJSON().category_id;row.splits=[];
    } else if (path.endsWith('/splits')) {
      const row=transactions.find(item=>item.id===Number(path.split('/')[3]));
      row.splits=request.postDataJSON().splits;row.category_id=null;
    } else if (path==='/api/categories' && request.method()==='POST') {
      categories.push({...request.postDataJSON(),id:3,spent:0,earned:0,enabled:true});
    } else if (/^\/api\/categories\/\d+$/.test(path)) {
      categoryPatchCount++;
      if (failCategory) {await route.fulfill({status:500,json:{detail:'test failure'}});return;}
      const category=categories.find(item=>item.id===Number(path.split('/').at(-1)));
      const settings=request.postDataJSON();
      if (settings.name && categories.some(item=>item.id!==category.id && item.name===settings.name)) {
        await route.fulfill({status:409,json:{detail:'Category already exists for this month'}});return;
      }
      lastCategoryPatch=request.postDataJSON();
      Object.assign(category,request.postDataJSON());
    }
    await route.fulfill({json:result});
  });
  await page.route('**/auth/status',route=>route.fulfill({json:{enabled:true,authenticated:true}}));
  await page.route('**/auth/demo',route=>route.fulfill({contentType:'text/html',body:'Demo entry'}));
  const base='http://127.0.0.1:'+server.address().port;
  await page.goto(base);
  await page.locator('.transaction-row').first().waitFor();
  assert.equal(await page.locator('#last-sync-time').textContent(),'Ещё не выполнялась');
  assert.match(await page.locator('#total-balance').innerText(),/999/);
  assert.equal(await page.locator('#recent-transactions img').count(),0);
  assert.equal(await page.locator('#budget-summary [data-overview-category]').count(),categories.length);
  await page.locator('[data-overview-category="1"]').click();
  await page.waitForFunction(()=>location.hash==='#operations');
  assert.equal(await page.locator('#category-filter').inputValue(),'1');
  assert.equal(await page.locator('#transactions .transaction-row').count(),0);
  await page.locator('[data-view="overview"]').click();
  await page.locator('#chart-range').selectOption('1');
  assert.equal(await page.locator('#cashflow .chart-income').count(),1);
  await page.locator('#chart-range').selectOption('30');
  const layoutWidths = [1440,1100,900,768,761,760,375];
  const assertHeaderControlsFit = async width => {
    for (const control of await page.locator('.sidebar-footer select, .sidebar-footer button:not([hidden])').all()) {
      await control.scrollIntoViewIfNeeded();
      const box = await control.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= width,
        `${width}px: header control outside viewport: ${await control.getAttribute('aria-label') || await control.textContent()}`);
    }
  };
  for (const width of layoutWidths) {
    await page.setViewportSize({width,height:900});
    for (const theme of ['light','dark']) {
      await assertHeaderControlsFit(width);
      if(await page.locator('html').getAttribute('data-theme')!==theme) await page.locator('[data-theme-toggle]').click();
      await assertHeaderControlsFit(width);
      for (const view of ['overview','operations','categories','plan','accounts']) {
        await page.locator('[data-view="'+view+'"]').click();
        await page.locator('[data-page="'+view+'"]').waitFor({state:'visible'});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,width+' '+theme+' '+view);
      }
    }
  }
  await page.reload();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.locator('[data-currency-edit="2"]').click();
  await page.locator('#account-currency-2').fill('usd');
  assert.equal(await page.locator('#account-currency-2').inputValue(),'USD');
  await page.evaluate(() => window.bankEvents.dispatchEvent(new Event('changed')));
  await page.waitForTimeout(50);
  assert.equal(await page.locator('#account-currency-2').inputValue(),'USD');
  await page.locator('[data-currency-form="2"] button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('[data-currency-edit="2"]')?.parentElement.querySelector('strong')?.textContent==='USD');
  assert.equal(accounts[1].currency,'USD');
  failMonitor=true;
  await page.locator('[data-account-id="1"]').uncheck();
  await page.waitForFunction(()=>{const input=document.querySelector('[data-account-id="1"]');return input.checked&&!input.disabled;});
  assert.equal(accounts[0].monitored,true);
  failMonitor=false;
  await page.locator('[data-balance-edit="1"]').click();
  await page.locator('#account-balance-1').fill('9999999999999999.98');
  failBalance=true;
  await page.locator('[data-balance-form="1"] button[type=submit]').click();
  await page.locator('.account-balance-error').waitFor();
  assert.equal(await page.locator('#account-balance-1').inputValue(),'9999999999999999.98');
  failBalance=false;
  await page.locator('[data-balance-form="1"] button[type=submit]').click();
  await page.locator('[data-balance-edit="1"]').waitFor();
  assert.equal(accounts[0].balance,'9999999999999999.98');
  // Saved balances must refresh everywhere without a page reload after sync.
  const assertDisplayedBalance = async balance => {
    await page.waitForFunction(expected => {
      const selectors = ['[data-account-balance="1"]', '#total-balance > span:first-child',
        '#accounts-total > span:first-child', '#account-summary .compact-account:first-child b'];
      return selectors.every(selector => document.querySelector(selector)?.textContent.replace(/\s/g,'') === expected);
    }, balance + 'EUR');
    assert.equal((await page.locator('[data-account-balance="2"]').textContent()).replace(/\s/g,''),'10,25USD');
  };
  await page.locator('[data-balance-edit="1"]').click();
  await page.locator('#account-balance-1').fill('100.00');
  await page.locator('[data-balance-form="1"] button[type=submit]').click();
  await assertDisplayedBalance('100,00');
  syncBalances.push('90.00','95.25');
  for (const balance of ['90,00','95,25','95,25']) {
    await page.locator('#sync').click();
    await page.waitForFunction(() => !document.querySelector('#sync').disabled);
    await assertDisplayedBalance(balance);
  }
  assert.equal(syncCount,3);
  const assertSyncTime = async () => {
    await page.waitForFunction(expected => document.querySelector('#last-sync-time').dateTime === expected, lastSuccessAt);
  };
  await assertSyncTime();
  assert.match(await page.locator('#last-sync-time').textContent(),/09\.11\.2026.*14:03/);
  failSync=true;
  await page.locator('#sync').click();
  await page.waitForFunction(() => !document.querySelector('#sync').disabled);
  assert.equal(await page.locator('#last-sync-time').getAttribute('datetime'),lastSuccessAt);
  failSync=false;
  await page.reload();
  await assertSyncTime();
  await assertDisplayedBalance('95,25');
  // A background synchronization uses the server's change event instead.
  accounts[0].balance = '80.25';
  await page.evaluate(() => window.bankEvents.dispatchEvent(new Event('changed')));
  await assertDisplayedBalance('80,25');
  lastSuccessAt = '2026-11-09T13:45:00Z';
  await page.evaluate(() => window.bankEvents.dispatchEvent(new Event('changed')));
  await assertSyncTime();
  assert.match(await page.locator('#last-sync-time').textContent(),/15:45/);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('[data-view="operations"]').click();
  assert.equal(await page.locator('#transactions [data-transaction="1"] .transaction-title').textContent(),'Transport Company');
  await page.locator('#transaction-search').fill('transport');
  assert.equal(await page.locator('#transactions .transaction-row').count(),1);
  await page.locator('#transaction-search').fill('long bank');
  assert.equal(await page.locator('#transactions .transaction-row').count(),1);
  // Dismiss drafts from every side, including narrow screens, without API writes.
  const writesBeforeDismiss = writes.length;
  for (const width of [1440,375]) {
    await page.setViewportSize({width,height:900});
    for (const side of ['left','right','top','bottom']) {
      await page.locator('#transactions .transaction-row').click();
      const dialog = page.locator('#transaction-dialog');
      assert.equal(await page.locator('#assign-form select').inputValue(),'');
      await page.locator('#assign-form select').selectOption('1');
      const box = await dialog.boundingBox();
      await page.mouse.click(box.x + 4,box.y + 4); // Dialog padding is inside.
      assert.equal(await dialog.isVisible(),true);
      const point = {
        left:[box.x / 2,box.y + box.height / 2],
        right:[(box.x + box.width + width) / 2,box.y + box.height / 2],
        top:[width / 2,box.y / 2],
        bottom:[width / 2,(box.y + box.height + 900) / 2],
      }[side];
      if (side === 'left') {
        await page.locator('#detail-title').hover();
        await page.mouse.down();
        await page.mouse.move(...point);
        await page.mouse.up();
        assert.equal(await dialog.isVisible(),true,'Dragging out must not dismiss a dialog');
      }
      await page.mouse.click(...point);
      await dialog.waitFor({state:'hidden'});
      assert.equal(transactions[1].category_id,null);
    }
    await page.locator('#transactions .transaction-row').click();
    assert.equal(await page.locator('#assign-form select').inputValue(),'');
    await page.locator('#detail-split').click();
    await page.locator('#split-rows input').first().fill('1');
    const box = await page.locator('#split-modal').boundingBox();
    await page.mouse.click(box.x / 2,box.y + box.height / 2);
    await page.locator('#split-modal').waitFor({state:'hidden'});
    assert.deepEqual(transactions[1].splits,[]);
    await page.locator('#transactions .transaction-row').click();
    await page.locator('#detail-split').click();
    assert.equal(Number(await page.locator('#split-rows input').first().inputValue()),15);
    await page.keyboard.press('Escape');
    await page.locator('#split-modal').waitFor({state:'hidden'});
  }
  assert.equal(writes.length,writesBeforeDismiss,'Dismissal must not save drafts');
  await page.locator('#transactions .transaction-row').click();
  await page.locator('#assign-form select').selectOption('1');
  await page.locator('#assign-form button[type=submit]').click();
  await page.locator('#transaction-dialog').waitFor({state:'hidden'});
  assert.equal(transactions[1].category_id,1);
  await page.locator('#transactions .transaction-row').click();
  await page.locator('#detail-split').click();
  await page.locator('#split-modal').waitFor({state:'visible'});
  await page.locator('#split-save').click();
  await page.locator('#split-modal').waitFor({state:'hidden'});
  assert.equal(transactions[1].splits.length,2);
  assert.equal(transactions[1].splits.reduce((sum,row)=>sum+row.amount,0),30);
  const balanceBeforeExclusion = await page.locator('#total-balance').innerText();
  await page.locator('#transactions .transaction-row').click();
  failStatistics=true;
  await page.locator('#detail-statistics').uncheck();
  await page.locator('#transaction-dialog [data-error]').waitFor();
  assert.equal(await page.locator('#detail-statistics').isChecked(),true);
  failStatistics=false;
  await page.locator('#detail-statistics').uncheck();
  await page.waitForFunction(()=>document.querySelector('#spent').textContent==='—');
  assert.equal(await page.locator('#total-balance').innerText(),balanceBeforeExclusion);
  assert.equal(transactions[1].splits.length,2);
  assert.equal(await page.locator('.chart-expense').evaluateAll(bars=>bars.every(bar=>bar.getAttribute('height')==='0')),true);
  await page.locator('[data-close-detail]').click();
  await page.locator('#transactions .statistics-excluded').waitFor();
  await page.reload();
  await page.locator('#transactions [data-transaction="1"] .statistics-excluded').waitFor();
  await page.locator('#transactions [data-transaction="2"]').click();
  await page.locator('#detail-statistics').uncheck();
  await page.waitForFunction(()=>document.querySelector('#earned').textContent==='—');
  assert.equal(await page.locator('#cashflow .empty-state').count(),1);
  await page.locator('#detail-statistics').check();
  await page.waitForFunction(()=>document.querySelector('#earned').textContent!=='—');
  await page.locator('[data-close-detail]').click();
  await page.locator('#transactions [data-transaction="1"]').click();
  assert.equal(await page.locator('#detail-statistics').isChecked(),false);
  await page.locator('#detail-statistics').check();
  await page.waitForFunction(()=>document.querySelector('#spent').textContent!=='—');
  await page.locator('[data-close-detail]').click();
  await page.locator('#transaction-search').fill('long bank');
  await page.locator('#tab-days').click();
  assert.equal(await page.locator('.day-heading').count(),1);
  await page.locator('#category-filter').selectOption('2');
  assert.equal(await page.locator('#transactions .transaction-row').count(),1);
  await page.locator('[data-view="categories"]').click();
  await page.locator('#category-form [name=name]').fill('Freelance');
  await page.locator('#category-form [name=budget]').fill('-500');
  await page.locator('#category-form button[type=submit]').click();
  await page.locator('[data-manager-category="3"]').waitFor();
  assert.equal(categories[2].budget,-500);
  const categoryName = page.locator('[data-category-name="3"]');
  await categoryName.fill('  Consulting  ');
  await categoryName.press('Enter');
  await page.waitForFunction(()=>document.querySelector('[data-category-name="3"]').value==='Consulting'&&!document.querySelector('[data-category-name="3"]').disabled);
  assert.deepEqual(lastCategoryPatch,{name:'Consulting'});
  assert.equal(categories[2].budget,-500);
  assert.equal(await page.locator('#category-filter option[value="3"]').textContent(),`${categories[2].icon} Consulting`);
  const savedPatchCount = categoryPatchCount;
  await categoryName.fill('Cancelled edit');
  await categoryName.press('Escape');
  assert.equal(await categoryName.inputValue(),'Consulting');
  await categoryName.fill('   ');
  await categoryName.press('Enter');
  assert.equal(await categoryName.evaluate(input=>input.checkValidity()),false);
  await categoryName.press('Escape');
  await categoryName.fill('Consulting');
  await categoryName.press('Enter');
  assert.equal(categoryPatchCount,savedPatchCount);
  await categoryName.fill('Food');
  await categoryName.press('Tab');
  await page.locator('[data-manager-category="3"] [role="alert"]').waitFor();
  assert.equal(await categoryName.inputValue(),'Consulting');
  assert.equal(categories[2].name,'Consulting');
  const escapedCategoryName = 'Consulting <b>&"';
  failCategoryReload=true;
  await categoryName.fill(escapedCategoryName);
  await categoryName.press('Tab');
  await page.waitForFunction(name=>document.querySelector('[data-category-name="3"]').value===name&&!document.querySelector('[data-category-name="3"]').disabled,escapedCategoryName);
  assert.equal(await page.locator('[data-manager-category="3"] b').count(),0);
  assert.equal(failCategoryReload,false);
  assert.equal(await page.locator('[data-manager-category="3"] [role="alert"]').count(),0);
  await page.reload();
  await page.locator('[data-view="categories"]').click();
  await categoryName.waitFor();
  assert.equal(await categoryName.inputValue(),escapedCategoryName);
  failCategory=true;
  await categoryName.fill('Failed rename');
  await categoryName.press('Enter');
  await page.locator('[data-manager-category="3"] [role="alert"]').waitFor();
  assert.equal(await categoryName.inputValue(),escapedCategoryName);
  assert.equal(categories[2].name,escapedCategoryName);
  await page.locator('[data-category-enabled="3"]').uncheck();
  await page.waitForFunction(()=>{const input=document.querySelector('[data-category-enabled="3"]');return input.checked&&!input.disabled;});
  await page.locator('[data-category-budget="3"]').fill('-999');
  await page.locator('[data-category-budget="3"]').press('Tab');
  await page.waitForFunction(()=>document.querySelector('[data-category-budget="3"]').value==='-500');
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-category-delete="3"]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-category-delete="3"]').disabled);
  assert.equal(await page.locator('[data-manager-category="3"]').count(),1);
  failCategory=false;
  await page.locator('[data-category-enabled="3"]').uncheck();
  await page.waitForFunction(()=>document.querySelector('[data-manager-category="3"] .expense-meta').textContent==='Выключена');
  assert.deepEqual(lastCategoryPatch,{enabled:false});
  for (const language of ['en','et','ru']) {
    await page.locator('#language').selectOption(language);
    await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
    assert.equal(await page.locator('#last-sync > span').textContent(),{en:'Last synchronization',et:'Viimane sünkroonimine',ru:'Последняя синхронизация'}[language]);
  }
  await page.locator('[data-view="plan"]').click();
  assert.match((await page.locator('#plan-forecast').innerText()).replace(/\s/g,''), /10000000000000329,99/);
  const foodPlan = page.locator('[data-plan-category="1"]');
  await foodPlan.locator('[data-plan-setting="forecast_mode"]').selectOption('payment');
  await page.waitForFunction(()=>document.querySelector('[data-plan-category="1"] [data-plan-setting="payment_status"]').disabled===false);
  assert.equal(categories[0].forecast_mode,'payment');
  await foodPlan.locator('[data-plan-setting="payment_status"]').selectOption('open');
  await page.waitForFunction(()=>document.querySelector('[data-plan-category="1"] [data-plan-setting="payment_status"]').value==='open'&&!document.querySelector('[data-plan-category="1"] [data-plan-setting="payment_status"]').disabled);
  assert.equal(categories[0].payment_status,'open');
  await page.locator('#add-plan-item').click();
  const planForm = page.locator('#plan-item-form');
  const expenseName = 'Ремонт <b> & "авто"';
  await planForm.locator('[name=name]').fill(expenseName);
  await planForm.locator('[name=amount]').fill('90.25');
  await planForm.locator('[name=payment_status]').selectOption('open');
  assert.equal(await planForm.locator('[name=transaction_ids][value="2"]').count(),0,'Income is not offered for an expense');
  await planForm.locator('[name=transaction_ids][value="1"]').check();
  failPlanWrite=true;
  await planForm.locator('[type=submit]').click();
  await page.locator('#plan-item-dialog [data-error]').waitFor();
  assert.equal(await planForm.locator('[name=name]').inputValue(),expenseName);
  assert.equal(planItems.length,0);
  failPlanWrite=false;
  await planForm.locator('[type=submit]').click();
  await page.locator('#plan-item-dialog').waitFor({state:'hidden'});
  assert.equal(planItems[0].amount,'90.25');
  assert.deepEqual(planItems[0].transaction_ids,[1]);
  assert.equal(planItems[0].month,month);
  assert.equal(await page.locator('#plan-items .plan-row-heading strong b').count(),0,'Plan names are escaped');
  transactions[1].excluded_from_statistics=true;
  await page.reload();
  await page.locator('[data-edit-plan="1"]').click();
  const staleLink = planForm.locator('[data-unavailable-link][value="1"]');
  assert.equal(await staleLink.isChecked(),true,'Unavailable links stay visible until explicitly removed');
  assert.equal(await planForm.evaluate(form=>form.checkValidity()),false);
  const beforeStaleSave = writes.length;
  await planForm.locator('[type=submit]').click();
  assert.equal(writes.length,beforeStaleSave,'An invalid link cannot be silently dropped on save');
  await staleLink.uncheck();
  assert.equal(await planForm.evaluate(form=>form.checkValidity()),true);
  await page.locator('#plan-item-cancel').click();
  assert.deepEqual(planItems[0].transaction_ids,[1]);
  transactions[1].excluded_from_statistics=false;
  await page.reload();
  await page.locator('[data-edit-plan="1"]').waitFor();
  await page.locator('#add-plan-item').click();
  assert.equal(await planForm.locator('[name=transaction_ids][value="1"]').count(),0,'Reserved operations are not offered twice');
  await planForm.locator('[name=name]').fill('Разовый доход');
  await planForm.locator('[name=direction]').selectOption('CRDT');
  await planForm.locator('[name=amount]').fill('1500.10');
  assert.equal(await planForm.locator('[name=transaction_ids][value="2"]').count(),1);
  failPlanReload=true;
  await planForm.locator('[type=submit]').click();
  await page.locator('#plan-item-dialog [data-error]').waitFor();
  assert.equal(planItems.length,2,'POST succeeded even though reload failed');
  await planForm.locator('[type=submit]').click();
  await page.locator('#plan-item-dialog').waitFor({state:'hidden'});
  assert.equal(planItems.length,2,'Retry after failed reload must update, not duplicate the item');
  assert.equal(planItems[1].direction,'CRDT');
  for (const width of [1440,375]) {
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Plan with items fits '+width);
    if (process.env.PLAN_SCREENSHOT_DIR) {
      await mkdir(process.env.PLAN_SCREENSHOT_DIR,{recursive:true});
      await page.screenshot({path:process.env.PLAN_SCREENSHOT_DIR+'/plan-'+width+'.png',fullPage:true});
    }
  }
  await page.locator('[data-edit-plan="1"]').click();
  assert.equal(await planForm.locator('[name=transaction_ids][value="1"]').isChecked(),true);
  await planForm.locator('[name=transaction_ids][value="1"]').uncheck();
  await planForm.locator('[name=payment_status]').selectOption('closed');
  await planForm.locator('[type=submit]').click();
  await page.locator('#plan-item-dialog').waitFor({state:'hidden'});
  assert.deepEqual(planItems[0].transaction_ids,[]);
  assert.equal(planItems[0].payment_status,'closed');
  const beforeCancel = writes.length;
  await page.locator('[data-edit-plan="1"]').click();
  await planForm.locator('[name=amount]').fill('999');
  await page.keyboard.press('Escape');
  await page.locator('#plan-item-dialog').waitFor({state:'hidden'});
  assert.equal(writes.length,beforeCancel);
  assert.equal(planItems[0].amount,'90.25');
  failPlanWrite=true;
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-delete-plan="1"]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-delete-plan="1"]').disabled);
  assert.equal(planItems.length,2);
  failPlanWrite=false;
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-delete-plan="1"]').click();
  await page.locator('[data-edit-plan="1"]').waitFor({state:'detached'});
  assert.equal(planItems.length,1);
  for (const language of ['en','et','ru']) {
    await page.locator('#language').selectOption(language);
    await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
    assert.equal(await page.locator('#add-plan-item').innerText(),{en:'Add',et:'Lisa',ru:'Добавить'}[language]);
  }
  emptyMonth=true;
  await page.locator('#category-month').fill('2025-01');
  await page.waitForFunction(()=>!document.querySelector('#plan-forecast .forecast-value'));
  assert.match(await page.locator('#plan-forecast').innerText(),/текущего месяца/);
  await page.locator('[data-view="operations"]').click();
  await page.locator('#transactions .empty-state').waitFor();
  expireEvents=true;
  await page.evaluate(()=>sessionStorage.setItem('real-events','1'));
  await page.reload();
  await page.waitForURL(base+'/auth/demo');
  await page.goto(base+'/static/login.html');
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  assert.equal(await page.locator('[name=remember_device]').isChecked(),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('[data-theme-toggle]').click();
  await page.reload();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  assert.deepEqual(errors,[]);
  console.log('Browser regression passed: themes, 70 layouts, header controls at responsive boundaries, filters, assignments, splits, budgets, monthly plan CRUD and links, mutation failure/retry, languages, SSE expiry, empty state and sign-in.');
} finally {
  await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
