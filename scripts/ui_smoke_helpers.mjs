// Legacy suites pre-authorize confirmations against temporary mocked services only.
// ui_polish_smoke exercises the actual Cancel/Confirm keyboard flow separately.
export async function approveMockConfirmations(page) {
 page.on('dialog',dialog=>dialog.accept().catch(()=>{}))
 await page.addInitScript(()=>{
  new MutationObserver(()=>{
   const dialog=document.querySelector('dialog[open][aria-label="Confirm action"]')
   const button=dialog&&[...dialog.querySelectorAll('button')].find(b=>b.textContent==='Confirm')
   if(button)button.click()
  }).observe(document,{childList:true,subtree:true,attributes:true,attributeFilter:['open']})
 })
}
export async function openAIPreferences(page) {
 const details=page.getByRole('region',{name:'AI assistant settings',exact:true}).locator('details.pm-ai-permissions')
 await details.waitFor({state:'attached'})
 if(!await details.evaluate(node=>node.open))await details.locator(':scope > summary').click()
}
export async function openAdvancedSettings(page) {
 await openAIPreferences(page)
 const summary=page.getByRole('region',{name:'AI assistant settings',exact:true}).locator('details').filter({has:page.getByText('Advanced Settings',{exact:true})}).locator('summary').first()
 await summary.waitFor()
 if(!await summary.evaluate(node=>node.parentElement.open))await summary.click()
}
