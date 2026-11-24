// These commands inspect the signed-in user's current input only.
export type LabelCommand = { applied: boolean; name: string | null }

export const sendCommand = /^(?:yes[, ]+)?(?:please\s+)?send(?:\s+(?:it|this|that|(?:(?:the|this|that|my)\s+)?(?:draft|email|message)))?(?:\s+now)?(?:,?\s+please)?[.!]?$/i

export function labelCommand(prompt: string, labels: readonly string[] = []): LabelCommand | undefined {
  const text = prompt.replace(/^please\s+/i, '').replace(/[.!]$/, '').trim()
  const relative = /^(apply|remove)\s+(?:it|that label|(?:the\s+)?suggested label)$/i.exec(text)
  if (relative) return { applied: relative[1].toLowerCase() === 'apply', name: null }

  const categorize = /^label\s+(?:it|this(?: email| message)?)\s+(?:as|with)\s+(.+)$/i.exec(text)
  if (categorize) return { applied: true, name: unquote(categorize[1]) }

  const explicit = /^(apply|add|remove)\s+(.+)$/i.exec(text)
  if (!explicit) return
  const applied = explicit[1].toLowerCase() !== 'remove'
  const target = /\s+(to|from)\s+(?:it|(?:this|the|the open|the current) (?:email|message))$/i.exec(explicit[2])
  if (target && target[1].toLowerCase() !== (applied ? 'to' : 'from')) return
  const object = target ? explicit[2].slice(0, target.index) : explicit[2]
  const name = object.replace(/^the\s+/i, '').replace(/^label\s+/i, '').replace(/\s+label$/i, '')
  const label = unquote(name)
  if (!target && name === object && !labels.some((labelName) => labelName.toLowerCase() === label.toLowerCase())) {
    return
  }
  return { applied, name: label }
}

function unquote(value: string) {
  return value.trim().replace(/^["'“‘`]|["'”’`]$/g, '').trim()
}
