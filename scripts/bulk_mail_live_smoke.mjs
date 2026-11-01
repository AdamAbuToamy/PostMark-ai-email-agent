// Real SQLite-backed selection/previews only. No confirm, step or Gmail writes.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',route=>{const r=route.request();if(r.method()==='GET')return route.continue();if(r.method()==='POST'&&/\/api\/bulk-mail\/(personal|university)\/preview$/.test(r.url())){writes.push(r.url());return route.continue()}return route.abort()})
 await page.goto(base+'/trash?account=personal');const area=page.getByRole('region',{name:'Trash',exact:true});await area.locator('.v5-managed-row').first().waitFor({timeout:60000})
 const rows=area.locator('.v5-managed-row'),checks=rows.getByRole('checkbox');await checks.nth(0).check();await checks.nth(1).check();await area.getByText('2 selected',{exact:true}).waitFor()
 const next=area.locator('.v5-mailbox-pagination').getByRole('button',{name:'Next',exact:true});if(await next.isEnabled()){const first=await rows.first().innerText();await next.click();await page.waitForFunction(text=>document.querySelector('.v5-managed-row')?.textContent!==text,first);await checks.nth(0).check();await area.getByText('3 selected',{exact:true}).waitFor()}
 await area.getByLabel('Search managed messages').fill('not-a-real-bulk-fixture-01293');await page.waitForTimeout(500);assert.equal(await area.locator('.v5-selection-bar').count(),0)
 await area.getByLabel('Search managed messages').fill('');await rows.first().waitFor();await area.getByRole('button',{name:'Select all visible',exact:true}).click()
 const response=page.waitForResponse(r=>r.url().endsWith('/api/bulk-mail/personal/preview')&&r.request().method()==='POST');await area.getByRole('button',{name:'Select all matching messages',exact:true}).click();const selected=await(await response).json();assert.ok(selected.matched>=selected.items.length);assert.equal(selected.operation,'restore');assert.equal(selected.approved,false)
 const preview=area.getByRole('region',{name:'Bulk action preview'});await preview.waitFor();assert.match(await preview.innerText(),new RegExp(`${selected.count} eligible of ${selected.matched}`))
 if(selected.next_offset!==null){await preview.getByRole('button',{name:'Next selection page'}).click();await preview.getByRole('button',{name:'Previous selection page'}).waitFor();assert.ok(await preview.getByRole('button',{name:'Previous selection page'}).isEnabled())}
 await area.getByRole('button',{name:'Delete permanently…',exact:true}).click();await preview.getByRole('textbox',{name:'Permanent deletion exact confirmation'}).waitFor();const confirmDelete=preview.getByRole('button',{name:/Confirm permanent deletion/});assert.ok(await confirmDelete.isDisabled());await preview.getByRole('textbox',{name:'Permanent deletion exact confirmation'}).fill('DELETE wrong');assert.ok(await confirmDelete.isDisabled())
 for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/bulk-mail-${width}.png`,fullPage:true})}
 await checks.nth(0).uncheck();await page.waitForFunction(()=>!document.querySelector('[aria-label="Bulk action preview"]'))
 await page.goto(base+'/categories/promotions?account=personal');await page.locator('.v5-selectable-row').first().waitFor({timeout:30000});const categoryChecks=page.locator('.v5-selectable-row').getByRole('checkbox');await categoryChecks.nth(0).check();await categoryChecks.nth(1).check();await page.getByText('2 selected',{exact:true}).waitFor()
 const beforePage=await page.locator('.v5-selectable-row').first().innerText();await page.locator('.v5-mailbox-pagination').getByRole('button',{name:'Next',exact:true}).click();await page.waitForFunction(text=>document.querySelector('.v5-selectable-row')?.textContent!==text,beforePage);await categoryChecks.nth(0).check();await page.getByText('3 selected',{exact:true}).waitFor();await page.getByRole('button',{name:'Deselect all',exact:true}).click();assert.equal(await page.locator('.v5-selection-bar').count(),0)
 assert.ok(writes.every(url=>url.endsWith('/preview')));assert.deepEqual(errors,[])
 console.log(JSON.stringify({result:'PASS',selected:selected.matched,scope:'real local Trash, cross-page checkboxes, cleared filters, exact frozen all-matching preview, paginated preview, permanent confirmation blocked, desktop/mobile',gmailMutations:0,postPaths:writes}))
}finally{await browser.close()}
