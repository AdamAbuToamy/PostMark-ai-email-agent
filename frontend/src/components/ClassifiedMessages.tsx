import type { ComponentProps } from 'react'
import { LocalCategory } from './LocalCategory'
type Props = Omit<ComponentProps<typeof LocalCategory>, 'categoryId' | 'title'> & { page: 'bills' | 'jobs' | 'courses' | 'promotions' }
const categories = { bills: 'bills', jobs: 'job_applications', courses: 'university', promotions: 'promotions' }
export function ClassifiedMessages({ page, ...props }: Props) {
  return <LocalCategory key={`${props.account}:${page}`} {...props} categoryId={categories[page]} title={page === 'jobs' ? 'Applications' : page === 'courses' ? 'University' : page} />
}
