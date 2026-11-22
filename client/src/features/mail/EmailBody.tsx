import DOMPurify from 'dompurify'
import { useEffect, useMemo, useState } from 'react'
import { getInlineImages } from './api'
import type { MailMessage } from './types'

const noImages: MailMessage['inlineImages'] = {}

function sanitizePreview(html: string) {
  const page = DOMPurify.sanitize(html, {
    WHOLE_DOCUMENT: true,
    RETURN_DOM: true,
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['base', 'meta', 'link', 'form', 'area', 'iframe', 'object', 'embed'],
    FORBID_ATTR: ['ping', 'download', 'attributionsrc', 'autofocus'],
  }) as Element | null
  if (!page?.querySelector('head')) return null

  for (const link of page.querySelectorAll('a[href]')) {
    if (/^https?:\/\//i.test(link.getAttribute('href') ?? '')) {
      link.setAttribute('target', '_blank')
      link.setAttribute('rel', 'noopener noreferrer')
    } else {
      link.removeAttribute('href')
    }
  }

  return page
}

function contentId(source: string) {
  const id = source.slice(4)
  try {
    return decodeURIComponent(id).replace(/^<|>$/g, '')
  } catch {
    return id
  }
}

function previewDocument(
  original: Element,
  images: MailMessage['inlineImages'],
  attachments: Record<string, string>,
) {
  const page = original.cloneNode(true) as Element
  for (const image of page.querySelectorAll('img[src]')) {
    const source = image.getAttribute('src') ?? ''
    const secureSource = source.replace(/^\s*http:\/\//i, 'https://')
    if (secureSource !== source) {
      image.setAttribute('src', secureSource)
      continue
    }
    if (!/^cid:/i.test(source)) continue
    const inline = images[contentId(source)]
    const data = inline?.data ?? attachments[inline?.attachmentId ?? '']
    image.removeAttribute('srcset')
    if (inline && typeof data === 'string') {
      const base64 = data.replaceAll('-', '+').replaceAll('_', '/')
      image.setAttribute('src', `data:${inline.mimeType};base64,${base64}`)
    } else {
      image.removeAttribute('src')
    }
  }

  const policy = document.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content = "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; form-action 'none'; base-uri 'none'"
  const referrer = document.createElement('meta')
  referrer.name = 'referrer'
  referrer.content = 'no-referrer'
  page.querySelector('head')?.prepend(policy, referrer)
  return '<!doctype html>' + page.outerHTML
}

export default function EmailBody({ message }: { message: MailMessage }) {
  const { id, html, body, inlineImages = noImages } = message
  const [attachments, setAttachments] = useState<Record<string, string>>({})
  const [unavailable, setUnavailable] = useState(false)
  const parsed = useMemo(() => (html ? sanitizePreview(html) : null), [html])
  const attachmentIds = useMemo(() => {
    const referenced = new Set<string>()
    for (const image of parsed?.querySelectorAll('img[src]') ?? []) {
      const source = image.getAttribute('src') ?? ''
      if (!/^cid:/i.test(source)) continue
      const inline = inlineImages[contentId(source)]
      if (inline?.attachmentId) referenced.add(inline.attachmentId)
    }
    return [...referenced]
  }, [parsed, inlineImages])

  useEffect(() => {
    if (!attachmentIds.length) return
    const controller = new AbortController()
    void getInlineImages(id, attachmentIds, controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return
        setAttachments(result)
        setUnavailable(attachmentIds.some((attachmentId) => !Object.hasOwn(result, attachmentId)))
      },
      () => {
        if (!controller.signal.aborted) setUnavailable(true)
      },
    )
    return () => controller.abort()
  }, [id, attachmentIds])

  const preview = useMemo(
    () => parsed ? previewDocument(parsed, inlineImages, attachments) : null,
    [parsed, inlineImages, attachments],
  )
  return preview ? (
    <>
      {unavailable && <p className="note" role="status">Some embedded images are unavailable.</p>}
      <iframe
        className="message-html"
        title="Email body"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={preview}
      />
    </>
  ) : (
    <pre>{body}</pre>
  )
}
