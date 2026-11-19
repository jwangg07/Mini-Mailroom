import DOMPurify from 'dompurify'
import { useMemo } from 'react'

function previewDocument(html: string) {
  const page = DOMPurify.sanitize(html, {
    WHOLE_DOCUMENT: true,
    RETURN_DOM: true,
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['base', 'meta', 'link', 'form', 'area', 'iframe', 'object', 'embed'],
    FORBID_ATTR: ['ping', 'download', 'attributionsrc', 'autofocus'],
  }) as Element | null
  const head = page?.querySelector('head')
  if (!page || !head) return null

  for (const link of page.querySelectorAll('a[href]')) {
    if (/^https?:\/\//i.test(link.getAttribute('href') ?? '')) {
      link.setAttribute('target', '_blank')
      link.setAttribute('rel', 'noopener noreferrer')
    } else {
      link.removeAttribute('href')
    }
  }

  const policy = document.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content =
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'"
  head.prepend(policy)
  return '<!doctype html>' + page.outerHTML
}

export default function EmailBody({ html, text }: { html: string | null; text: string }) {
  const preview = useMemo(() => (html ? previewDocument(html) : null), [html])
  return preview ? (
    <iframe
      className="message-html"
      title="Email body"
      sandbox="allow-popups allow-popups-to-escape-sandbox"
      referrerPolicy="no-referrer"
      srcDoc={preview}
    />
  ) : (
    <pre>{text}</pre>
  )
}
