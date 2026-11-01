import { useState } from 'react'
import { ChevronDown, type LucideIcon } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { DrawIcon } from './DrawIcon'
import type { Page } from '../routing'
interface Item { id: Page; title: string; icon: LucideIcon; count?:number }
export function SidebarNavigation({ items, page, onOpen }: { items: Item[]; page: Page; onOpen: (page: Page) => void }) {
  const [preference, setPreference] = useState(() => { try { return localStorage.getItem('postmark.custom-categories') !== 'closed' } catch { return true } })
  const [closedRoute, setClosedRoute] = useState<Page | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [query, setQuery] = useState('')
  const [focusTitle, setFocusTitle] = useState('')
  const reduced = useReducedMotion()
  const custom = items.filter(item => item.id.startsWith('category:')).sort((a,b)=>Number(b.id===page)-Number(a.id===page)||(b.count??0)-(a.count??0)||a.title.localeCompare(b.title))
  const expanded = preference || (page.startsWith('category:') && closedRoute !== page)
  const matches=custom.filter(item=>item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const visible=query.trim()||showAll?matches:matches.slice(0,4)
  function toggle() { const next = !expanded; setPreference(next); setClosedRoute(next ? null : page); try { localStorage.setItem('postmark.custom-categories', next ? 'open' : 'closed') } catch { /* Storage can be unavailable in private sessions. */ } }
  function row(item: Item) { return <motion.button key={item.id} className={`v5-nav-item ${page === item.id ? 'active' : ''}`} onClick={() => onOpen(item.id)} onFocus={()=>setFocusTitle(item.title)} onBlur={()=>setFocusTitle('')} whileHover={reduced ? undefined : { backgroundColor:'rgba(127,127,127,0.08)' }} title={item.title} aria-label={item.title} aria-describedby={focusTitle===item.title?'navigation-full-name':undefined} aria-current={page === item.id ? 'page' : undefined}>{page === item.id && <motion.span className="v5-nav-highlight" layoutId="active-navigation" transition={{ duration: reduced ? 0 : 0.16 }} />}<DrawIcon icon={item.icon} size={20} /><span className="pm-nav-label" dir="auto">{item.title}</span>{item.count!==undefined&&<small className="pm-nav-count" aria-hidden="true">{item.count}</small>}</motion.button> }
  function group(title: string, ids: string[]) { return <div className="v5-nav-group">{title&&<span className="v5-nav-heading">{title}</span>}{ids.flatMap(id=>items.filter(item=>item.id===id)).map(row)}</div> }
  return <nav className="v5-nav v5-grouped-nav" aria-label="Primary navigation">
    {group('', ['inbox','categories','groups','cleanup-review','trash','ai-activity'])}
    <section className="v5-nav-categories" aria-label="Category navigation"><div className="v5-nav-group">{items.filter(item => ['bills','jobs','courses','promotions'].includes(item.id)).map(row)}</div>
      {custom.length > 0 && <><button className="v5-custom-toggle" aria-expanded={expanded} aria-controls="custom-category-navigation" onClick={toggle}><span>Custom Categories</span><ChevronDown size={14} style={{ transform: `rotate(${expanded ? 0 : -90}deg)` }} /></button><div id="custom-category-navigation" hidden={!expanded}>{custom.length>6&&<input className="pm-nav-search" type="search" aria-label="Search custom categories" placeholder="Search categories" value={query} onChange={e=>setQuery(e.target.value)}/>}<div className="v5-nav-group">{visible.map(row)}</div>{!matches.length&&<p className="pm-nav-empty">No matching categories</p>}{custom.length>4&&!query.trim()&&<button className="pm-nav-show-all" aria-expanded={showAll} onClick={()=>setShowAll(v=>!v)}>{showAll?'Show fewer':`Show all (${custom.length})`}</button>}</div></>}
    <details className="v5-nav-group pm-nav-more"><summary>More</summary>{items.filter(item=>['overview','spam','chat','review','subscriptions','sync-history'].includes(item.id)).map(row)}</details>
    </section>
    <div className="v5-nav-system">{group('', ['settings'])}</div>
    {focusTitle&&<div className="pm-nav-focus-name" id="navigation-full-name" role="tooltip" dir="auto">{focusTitle}</div>}
  </nav>
}
