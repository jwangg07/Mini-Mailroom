import AccountPanel from './features/auth/AccountPanel'
import { useSession } from './features/auth/useSession'
import Mailbox from './features/mail/Mailbox'

export default function App() {
  const session = useSession()

  return (
    <main className={session.user ? 'mailroom mailroom-signed-in' : 'mailroom'}>
      <AccountPanel {...session} />
      {session.user && <Mailbox key={session.user.id} />}
    </main>
  )
}
