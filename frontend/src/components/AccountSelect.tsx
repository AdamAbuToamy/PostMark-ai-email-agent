import type { AccountId } from '../api/emails'
export function AccountSelect({ account, onChange }: { account: AccountId; onChange: (account: AccountId) => void }) {
  return <select className="v5-account-select" value={account} onChange={event => onChange(event.target.value as AccountId)} aria-label="Choose email account">
    <option value="personal">Personal Gmail</option><option value="university">BGU University</option>
  </select>
}
