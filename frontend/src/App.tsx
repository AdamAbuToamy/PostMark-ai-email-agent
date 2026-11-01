import { useCallback, useEffect, useMemo, useState, useRef, lazy, Suspense, type ComponentProps } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  ShieldAlert,
  Trash2,
  ListChecks,
  LayoutGrid,
  Inbox,
  ReceiptText,
  BriefcaseBusiness,
  GraduationCap,
  Megaphone,
  Settings2,
  Search,
  RefreshCw,
  ChevronRight,
  Mail,
  X,
  ArrowUpRight,
  Menu,
  XCircle,
} from 'lucide-react'

import { postLocal } from './api/actions'
import { emailApi, type AccountId, type EmailMessage } from './api/emails'
import { DrawIcon } from './components/DrawIcon'
import { SidebarNavigation } from './components/SidebarNavigation'
import { MessageDetail } from './components/MessageDetail'
import { MailboxMessageRow } from './components/MailboxMessageRow'
import type { Category } from './api/mailboxes'
import { CompactInsights } from './components/CompactInsights'
import { Chat } from './components/Chat'
import { Review } from './components/Review'
import { Subscriptions } from './components/Subscriptions'
import { useRoute, useRouteMessage, type Page } from './routing'
const SettingsPage=lazy(()=>import('./components/SettingsPage').then(module=>({default:module.SettingsPage})))
const MailManagementView=lazy(()=>import('./components/MailManagement').then(module=>({default:module.MailManagementView})))
const LazyAutomaticCleanup=lazy(()=>import('./components/AutomaticCleanup').then(module=>({default:module.AutomaticCleanup})))
function AutomaticCleanup(props:ComponentProps<typeof LazyAutomaticCleanup>){return <Suspense fallback={<p role="status">Loading cleanup settings…</p>}><LazyAutomaticCleanup {...props}/></Suspense>}
import { OrganizationPanel } from './components/OrganizationPanel'
import { CategoryManagement } from './components/CategoryManagement'
import { ClassifiedMessages } from './components/ClassifiedMessages'
import { DynamicCategory } from './components/DynamicCategory'
import { getCategoryIcon } from './components/categoryIcons'
const UsageDashboard=lazy(()=>import('./components/UsageDashboard').then(m=>({default:m.UsageDashboard})))
import {SyncHistory} from './components/SyncHistory'
import './styles/v5.css'

type DynamicPage = `category:${string}`

const navigation = [
  { id: 'overview', title: 'Overview', icon: LayoutGrid },
  { id: 'inbox', title: 'Inbox', icon: Inbox },
  { id: 'bills', title: 'Bills', icon: ReceiptText },
  { id: 'jobs', title: 'Jobs', icon: BriefcaseBusiness },
  { id: 'courses', title: 'Courses', icon: GraduationCap },
  { id: 'promotions', title: 'Promotions', icon: Megaphone },
  { id: 'chat', title: 'Chat', icon: Mail },
  { id: 'review', title: 'Needs Review', icon: ShieldAlert },
  { id: 'subscriptions', title: 'Subscriptions', icon: ReceiptText },
  { id: 'cleanup-review', title: 'Cleanup', icon: ListChecks },
  { id: 'trash', title: 'Trash', icon: Trash2 },
  { id: 'spam', title: 'Spam', icon: ShieldAlert },
  { id: 'categories', title: 'Categories', icon: LayoutGrid },
  { id: 'groups', title: 'Groups', icon: LayoutGrid },
  { id: 'ai-activity', title: 'AI Activity', icon: RefreshCw },
  { id: 'sync-history', title: 'Sync & History', icon: RefreshCw },
  { id: 'settings', title: 'Settings', icon: Settings2 },
] as const

const collections = [
  { page: 'bills', title: 'Bills & payments', subtitle: 'Invoices, due dates and payments', icon: ReceiptText, color: 'sand' },
  { page: 'jobs', title: 'Applications', subtitle: 'Track your next opportunity', icon: BriefcaseBusiness, color: 'rose' },
  { page: 'courses', title: 'University', subtitle: 'Courses, schedules and updates', icon: GraduationCap, color: 'blue' },
] as const

function getSender(sender: string) {
  return sender.split('<')[0].replace(/["']/g, '').trim() || sender
}

export default function App() {
  const [pageVisible, setPageVisible] = useState(!document.hidden)
  useEffect(() => {
    const update = () => setPageVisible(!document.hidden)
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  const { route, navigate, closeMessage } = useRoute()
  const { page, account } = route
  const previousAccount=useRef(account)
  useEffect(()=>{
    if(previousAccount.current===account)return
    const previous=previousAccount.current
    previousAccount.current=account
    void postLocal(`/api/chat/${previous}/deactivate`,{}).catch(()=>{})
  },[account])
  const { selected, error: messageError, loading: messageLoading } = useRouteMessage(account, route.messageId)
  const setSelected = useCallback((message: EmailMessage | null) => message ? navigate({ page, account: message.account_id, messageId: message.id,filter:route.filter }) : closeMessage(), [page, navigate, closeMessage,route.filter])

  const [categories, setCategories] = useState<Category[]>([])
  const [localRevision, setLocalRevision] = useState(0)
  const [categoryRevision, setCategoryRevision] = useState(0)
  const [categoriesLoaded, setCategoriesLoaded] = useState(false)

  const categorySnapshot=useRef<{account:AccountId;value:string}|null>(null)
  useEffect(() => {
    const controller = new AbortController()
    if(categorySnapshot.current?.account!==account){setCategories([]);setCategoriesLoaded(false)}
    async function refreshNavigation() {
      if(document.hidden)return
      try {
        const response=await fetch(`/api/category-mailbox/${account}/categories`,{signal:controller.signal})
        if(!response.ok)throw new Error('Categories unavailable')
        const catalog=await response.json() as Category[]
        if(controller.signal.aborted)return
        const value=JSON.stringify(catalog)
        if(categorySnapshot.current?.account===account&&categorySnapshot.current.value!==value)setLocalRevision(v=>v+1)
        categorySnapshot.current={account,value};setCategories(catalog);setCategoriesLoaded(true)
      }catch{ /* Preserve usable navigation while the service reconnects. */ }
    }
    void refreshNavigation()
    const timer=setInterval(()=>void refreshNavigation(),30000)
    return()=>{controller.abort();clearInterval(timer)}
  },[page,account,categoryRevision])

  const dynamicItems = categories.filter(category => (category.source === 'approved' || category.id === 'orders' || category.id === 'other') && !['bills', 'job_applications', 'university', 'promotions'].includes(category.id)).map(category => ({
    id: `category:${category.id}` as DynamicPage,
    title: category.name,
    icon: getCategoryIcon(category.icon_key),
    count: category.counts?.all,
  }))

  const navigationItems = [
    ...navigation.filter(item => item.id !== 'settings').map(item => {
      const ids:Record<string,string>={bills:'bills',jobs:'job_applications',courses:'university',promotions:'promotions'}
      const category=categories.find(c=>c.id===ids[item.id])
      return category?{...item,title:category.name}:item
    }).filter(item=>{const ids:Record<string,string>={bills:'bills',jobs:'job_applications',courses:'university',promotions:'promotions'};return !categoriesLoaded||!ids[item.id]||categories.some(c=>c.id===ids[item.id])}),
    ...dynamicItems,
    navigation.find(item => item.id === 'settings')!,
  ]

  const [messages, setMessages] = useState<EmailMessage[]>([])
  const [query, setQuery] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [globalQuery, setGlobalQuery] = useState('')
  const [globalResults, setGlobalResults] = useState<EmailMessage[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [syncStatus, setSyncStatus] = useState('')

  const reduceMotion = useReducedMotion()

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    async function load() {
      setLoading(true)
      setError('')
      setMessages([])

      try {
        const result = await emailApi.messages(account, controller.signal)
        if (active) setMessages(result)
      } catch (err) {
        if (active && !controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Unable to load messages')
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [account, refreshKey])

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(current => !current)
      }

      if (event.key === 'Escape') {
        setPaletteOpen(false)
        setSelected(null)
        setMenuOpen(false)
      }
    }

    window.addEventListener('keydown', handleKeyboard)
    return () => window.removeEventListener('keydown', handleKeyboard)
  }, [setSelected])

  useEffect(() => {
    if (!menuOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [menuOpen])

  useEffect(() => {
    if (!paletteOpen) return

    const controller = new AbortController()
    let active = true

    async function loadSearchData() {
      setSearchLoading(true)
      try {
        const results = await Promise.all([
          emailApi.messages('personal', controller.signal),
          emailApi.messages('university', controller.signal),
        ])
        if (active) setGlobalResults(results.flat())
      } catch {
        if (active && !controller.signal.aborted) setGlobalResults([])
      } finally {
        if (active) setSearchLoading(false)
      }
    }

    void loadSearchData()
    return () => {
      active = false
      controller.abort()
    }
  }, [paletteOpen])

  const searchResults = useMemo(() => {
    const term = globalQuery.trim().toLowerCase()
    if (!term) return []
    return globalResults.filter(m =>
      m.sender.toLowerCase().includes(term) ||
      m.subject.toLowerCase().includes(term)
    ).slice(0, 30)
  }, [globalResults, globalQuery])

  const filtered = useMemo(() => {
    const value = query.toLowerCase().trim()
    return messages.filter(message =>
      !value ||
      message.sender.toLowerCase().includes(value) ||
      message.subject.toLowerCase().includes(value)
    )
  }, [messages, query])

  const handleSync = async () => {
    if (syncing) return

    const targetAccount = account
    setSyncing(true)
    setSyncStatus('')

    try {
      const result = await emailApi.sync(targetAccount)
      setSyncStatus(
        `${result.fetched} messages fetched from ${targetAccount}. ` +
        `${result.stored} stored locally.`
      )
      setRefreshKey(value => value + 1)
    } catch (error) {
      setSyncStatus(
        error instanceof Error
          ? `Sync error: ${error.message}`
          : 'Unable to sync email.'
      )
    } finally {
      setSyncing(false)
    }
  }

  const handleAccountChange = (nextAccount: AccountId) => {
    void postLocal(`/api/chat/${account}/deactivate`,{}).then(()=>navigate({ page, account: nextAccount,filter:route.filter })).catch(()=>setSyncStatus('Could not expire pending chat approvals. Retry switching accounts.'))
  }
  const handleLocalChange = () => setLocalRevision(value => value + 1)
  const handleBatchChange = () => { setLocalRevision(value => value + 1); setCategoryRevision(value => value + 1) }
  const categoryId = page.startsWith('category:') ? page.slice('category:'.length) : ''
  const pageTitle = categories.find(category => category.id === categoryId)?.name ?? navigation.find(item => item.id === page)?.title ?? (page==='automatic-cleanup'?'Automatic Cleanup':'Mailbox')

  const openPage = (nextPage: Page) => {
    navigate({ page: nextPage, account })
    setMenuOpen(false)
    setPaletteOpen(false)
  }

  const animation = reduceMotion
    ? { duration: 0 }
    : { type: 'spring' as const, stiffness: 320, damping: 30 }

  return (
    <div className="v5-app">

      <div className="v5-floating-doodles" aria-hidden="true" data-paused={!pageVisible}>
        {[
          ['✉️', 'd1'], ['📎', 'd2'], ['✏️', 'd3'],
          ['🧾', 'd4'], ['📚', 'd5'], ['💼', 'd6'],
          ['🎓', 'd7'], ['🔔', 'd8'], ['📩', 'd9'],
          ['🗂️', 'd10'], ['📬', 'd11'], ['📌', 'd12'],
        ].map(([symbol, name]) => (
          <span key={name} className={`v5-doodle ${name}`}>
            {symbol}
          </span>
        ))}
      </div>

      <AnimatePresence>
        {menuOpen && (
          <motion.button
            className="v5-menu-backdrop"
            aria-label="Close navigation"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setMenuOpen(false)}
          />
        )}
      </AnimatePresence>

      <motion.aside
        className="v5-sidebar"
        aria-hidden={!menuOpen}
        inert={!menuOpen}
        initial={false}
        animate={{ x: menuOpen ? '0%' : '-100%' }}
        transition={animation}
      >
        <div className="v5-brand">
          <div className="v5-brand-icon"><Mail size={22} /></div>
          <span>POSTMARK<span className="v5-brand-dot">.</span></span>
        </div>

        <SidebarNavigation key={account} items={navigationItems} page={page} onOpen={openPage} />

        <button className="v5-collapse" onClick={() => setMenuOpen(false)}>
          <XCircle size={19} />
          <span>Close navigation</span>
        </button>
      </motion.aside>

      <main className="v5-main">
        <header className="v5-topbar">
          <div className="v5-location">
            <motion.button
              className="v5-menu-trigger"
              onClick={() => setMenuOpen(true)}
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              whileHover={{ rotate: -5, scale: 1.07 }}
              whileTap={{ scale: 0.9 }}
            >
              <Menu size={21} />
            </motion.button>
            Workspace <ChevronRight size={14} /> <strong>{pageTitle}</strong>
          </div>

          <div className="v5-top-actions">
            <motion.button className="v5-command-button" aria-label="Quick search" onClick={() => setPaletteOpen(true)} whileTap={{ scale: 0.96 }}>
              <Search size={17} />
              <span>Quick search</span>
              <kbd>Ctrl K</kbd>
            </motion.button>

            <div className="v5-user-avatar">A</div>
          </div>
        </header>

        <div className="v5-content">
          <AnimatePresence mode="wait">
            <motion.div
              className="pm-page-stack"
              key={page}
              initial={reduceMotion ? false : { opacity: 0, y: 17 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: -9 }}
              transition={{ duration: reduceMotion ? 0 : 0.25 }}
            >
              <div className="v5-heading">
                <div>
                  <span className="v5-overline"></span>
                  <h1 tabIndex={-1}>{page === 'overview' ? 'Overview' : pageTitle}</h1>
                  <p>{page === 'overview' ? '' : ''}</p>
                </div>

                <motion.button className="v5-refresh" onClick={() => { setRefreshKey(value=>value+1); setLocalRevision(value=>value+1); window.dispatchEvent(new Event('postmark:refresh')) }} disabled={loading || syncing} whileTap={{ scale: 0.94 }}>
                  <RefreshCw size={17} className={loading || syncing ? 'v5-spinning' : ''} />
                  Refresh
                </motion.button>
                {(page==='inbox'||page==='overview')&&<button className="v5-small-button" disabled={syncing} onClick={()=>void handleSync()}>Sync mail…</button>}
              </div>

              {messageLoading && <p role="status" className="v5-mailbox-feedback">Opening message…</p>}
              {messageError && <p role="alert" className="v5-mailbox-feedback error">{messageError}<button onClick={closeMessage}>Close</button></p>}
              {syncStatus && (
                <div className="v5-sync-status" role="status">
                  {syncStatus}
                  <button
                    onClick={() => setSyncStatus('')}
                    aria-label="Dismiss sync status"
                  >
                    <X size={15} />
                  </button>
                </div>
              )}

              {(page === 'overview'||page==='inbox') && <OrganizationPanel key={`overview-${account}`} compact account={account} onOpen={setSelected} onChanged={handleBatchChange}/>}
              {page === 'overview' && (
                <div className="v5-collections">
                  {collections.map((item, index) => {
                    const Icon = item.icon
                    return (
                      <motion.button
                        key={item.page}
                        className={`v5-collection v5-${item.color}`}
                        onClick={() => openPage(item.page)}
                        initial={reduceMotion ? false : { opacity: 0, y: 22 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: reduceMotion ? 0 : index * 0.075 }}
                        whileHover={reduceMotion ? undefined : { y: -5 }}
                        whileTap={reduceMotion ? undefined : { scale: 0.98 }}
                      >
                        <div className="v5-collection-art"><DrawIcon icon={Icon} size={44} delay={0.5 + index * 0.2} /></div>
                        <div className="v5-collection-bottom">
                          <div>
                            <h3>{item.title}</h3>
                            <p>{item.subtitle}</p>
                          </div>
                          <ArrowUpRight size={21} />
                        </div>
                      </motion.button>
                    )
                  })}
                </div>
              )}

              {(page === 'overview' || page === 'inbox') ? (
                <section className="v5-mail-section">
                  <CompactInsights key={account} account={account} messages={messages} onOpen={setSelected} />

                  <div className="v5-section-header">
                    <div>
                      <h2>{page === 'overview' ? 'Recent messages' : 'Your inbox'}</h2>
                      <span>{filtered.length} locally stored messages shown</span>
                    </div>

                    <select
                      className="v5-account-select"
                      value={account}
                      onChange={event => handleAccountChange(event.target.value as AccountId)}
                      aria-label="Choose email account"
                    >
                      <option value="personal">Personal Gmail</option>
                      <option value="university">BGU University</option>
                    </select>
                  </div>

                  <div className="v5-search-field">
                    <Search size={18} />
                    <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search your messages..." />
                    {query && <button onClick={() => setQuery('')} aria-label="Clear search"><X size={16} /></button>}
                  </div>

                  {loading ? (
                    <div className="v5-empty"><RefreshCw className="v5-spinning" /> Loading messages...</div>
                  ) : error ? (
                    <div className="v5-empty">Connection error: {error}</div>
                  ) : filtered.length === 0 ? (
                    <div className="v5-empty"><Inbox size={27} /> No messages found.</div>
                  ) : (
                    <div className="v5-message-list">
                      {filtered.map(message => (
                        <MailboxMessageRow key={`${message.account_id}:${message.id}`} message={message} onOpen={setSelected} />
                      ))}
                    </div>
                  )}
                </section>
              ) : page === 'groups' ? (<OrganizationPanel key={`groups-${account}`} account={account} onOpen={setSelected} onChanged={handleBatchChange}/>) : page === 'ai-activity' ? (<Suspense fallback={<p role="status">Loading activity…</p>}><UsageDashboard key={account} account={account}/></Suspense>) : page === 'categories' ? (<><CategoryManagement key={account} account={account} onAccountChange={handleAccountChange} onCategoriesChanged={active => { setCategories(active); setCategoryRevision(value => value + 1) }} onOpen={id => openPage(`category:${id}`)} revision={localRevision + refreshKey} /></>) : page === 'chat' ? (<Chat key={`${account}-${refreshKey}`} account={account} onAccountChange={handleAccountChange} onOpen={setSelected} onChanged={handleBatchChange} />) : page === 'review' ? (<Review key={`${account}-${refreshKey}`} account={account} onAccountChange={handleAccountChange} onOpen={setSelected} onChanged={handleBatchChange} />) : page === 'subscriptions' ? (<Subscriptions key={`${account}-${refreshKey}`} account={account} onAccountChange={handleAccountChange} onOpen={setSelected} onChanged={handleBatchChange} />) : page === 'not-found' ? (<div className="v5-empty"><h2>Page unavailable</h2><p>This link is invalid or no longer available.</p><button className="v5-small-button" onClick={() => openPage('inbox')}>Open inbox</button></div>) : page === 'cleanup-review'||page==='trash'||page==='spam' ? (<Suspense fallback={<p role="status">Loading mail management…</p>}><MailManagementView view={page==='spam'?'spam':page==='trash'?'trash':route.filter??'eligible'} key={`${account}-${page}-${route.filter??"eligible"}-${refreshKey}`} account={account} onAccountChange={handleAccountChange} onOpen={setSelected} onChanged={handleBatchChange}/></Suspense>) : page==='automatic-cleanup' ? (<AutomaticCleanup key={`cleanup-${account}`} account={account} onAccountChange={handleAccountChange} onOpen={setSelected} onChanged={handleBatchChange}/>) : page === 'sync-history' ? (<SyncHistory/>) : page === 'settings' ? (
                <Suspense fallback={<p role="status">Loading settings…</p>}><SettingsPage key={`settings-${account}`} account={account} onAccountChange={handleAccountChange} onOpen={setSelected} onChanged={handleBatchChange}/></Suspense>
              ) : page.startsWith('category:') && categoriesLoaded && !categories.some(category => category.id === categoryId) ? (<div className="v5-empty"><h2>Category unavailable</h2><p>This category was removed or has not been approved.</p><button className="v5-small-button" onClick={() => openPage('settings')}>Review categories in Settings</button></div>) : page.startsWith('category:') ? (
                <DynamicCategory
                  categoryId={page.slice('category:'.length)}
                  title={
                    categories.find(
                      category => `category:${category.id}` === page
                    )?.name ?? 'Category'
                  }
                  account={account}
                  onOpen={setSelected}
                  onAccountChange={handleAccountChange}
                  revision={localRevision + refreshKey}
                  onChanged={handleLocalChange}
                />
              ) : (
                <div className="v5-category-page">
                  <ClassifiedMessages
                    page={
                      page === 'bills' ||
                      page === 'jobs' ||
                      page === 'courses' ||
                      page === 'promotions'
                        ? page
                        : 'promotions'
                    }
                    account={account}
                    onOpen={setSelected}
                    onAccountChange={handleAccountChange}
                    revision={localRevision + refreshKey}
                    onChanged={handleLocalChange}
                  />
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <AnimatePresence>
        {selected && (
          <MessageDetail key={`${selected.account_id}:${selected.id}`} message={selected} onClose={() => setSelected(null)} onChanged={handleLocalChange} revision={localRevision + refreshKey} />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {paletteOpen && (
          <motion.div className="v5-overlay v5-palette-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setPaletteOpen(false)}>
            <motion.div className="v5-palette" initial={{ y: -20, scale: 0.97 }} animate={{ y: 0, scale: 1 }} exit={{ y: -15, opacity: 0 }} onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="Quick navigation">
              <div className="v5-palette-heading">
                <Search size={19} />
                Search emails
                <button onClick={() => setPaletteOpen(false)} aria-label="Close search"><X size={17} /></button>
              </div>

              <input
                className="v5-global-search"
                autoFocus
                value={globalQuery}
                onChange={event => setGlobalQuery(event.target.value)}
                placeholder="Search sender or subject..."
                aria-label="Search all stored emails"
              />

              <div className="v5-search-results">
                {searchLoading ? (
                  <p>Loading messages...</p>
                ) : !globalQuery.trim() ? (
                  <p>Type to search both accounts.</p>
                ) : searchResults.length === 0 ? (
                  <p>No matching messages.</p>
                ) : (
                  searchResults.map(message => (
                    <button
                      key={`${message.account_id}-${message.id}`}
                      onClick={() => {
                        navigate({ page: 'inbox', account: message.account_id, messageId: message.id })
                        setPaletteOpen(false)
                      }}
                    >
                      <Mail size={17} />
                      <span>
                        <strong>{getSender(message.sender)}</strong>
                        <small>{message.subject || '(No subject)'}</small>
                      </span>
                      <small>{message.account_id}</small>
                    </button>
                  ))
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
