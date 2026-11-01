import type { ComponentProps } from 'react'
import { LocalCategory } from './LocalCategory'
export function DynamicCategory(props: ComponentProps<typeof LocalCategory>) {
  return <LocalCategory key={`${props.account}:${props.categoryId}`} {...props} />
}
