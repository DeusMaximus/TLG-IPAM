import { TriangleAlert } from 'lucide-react'
import {
  agentServerUrl,
  containerForget,
  containerRun,
  releaseVersion,
  SERVER_URL_PLACEHOLDER,
  systemdForget,
  systemdInstall,
  VERSION_PLACEHOLDER,
} from '../utils/install'
import CopyBlock from './CopyBlock'
import InstallTabs, { Prerequisites } from './InstallTabs'

// The page's server address as an agent may be given it, or a placeholder (and a
// warning) when the page is on plain http across a network: no command it shows then
// sends a token in clear. `location` is the page's own (`window.location`). `secure` is
// the one test of which pages are warned, here and in the steps before a token is
// issued: `agentServerUrl`'s, by the protocol and hostname the browser gives.
export function serverUrlFor(location) {
  const secure = agentServerUrl(location)
  return { url: secure ?? SERVER_URL_PLACEHOLDER, secure: Boolean(secure) }
}

const warningCls = 'mt-3 text-xs text-amber-200'

// On such a page, before the request that issues an enrolment token (Add agent's
// Create, re-enrol's Issue): the server's answer brings the token to this browser, so it
// crosses the network in clear whatever the commands do afterwards. It warns; it does
// not stop the owner.
export function PlainHttpBeforeIssue() {
  return (
    <p role="note" className={warningCls} data-testid="agent-plain-http-before">
      This page is served over plain HTTP, so the enrolment token the next step shows reaches
      this browser across the network in clear, where anyone who can watch the traffic can read
      it and exchange it before the agent does. To keep it off the network, open this page over
      HTTPS, or on the server&apos;s own host, first. The agent will need the server&apos;s HTTPS
      address either way: the commands on the next step use{' '}
      <code>{SERVER_URL_PLACEHOLDER}</code> in its place.
    </p>
  )
}

// On such a page, the commands carry the placeholder. With `issued`, beside a token the
// server has just sent: it has crossed in clear already, and the agent token, which the
// exchange returns, crosses only if the exchange is made over plain http, which these
// commands never make. Without it (the update commands) no token is on show.
export function PlainHttpWarning({ issued }) {
  return (
    <div className={`${warningCls} space-y-1`} data-testid="agent-needs-https">
      {issued ? (
        <>
          <p>
            This page is served over plain HTTP, so the enrolment token above has already crossed
            the network in clear. Until the agent exchanges it, anyone who read it on the way can
            exchange it in the agent&apos;s place: run the agent soon. If its exchange is refused,
            someone may have made it first: revoke this agent&apos;s token, then re-enrol it from
            a page served over HTTPS or on the server&apos;s own host.
          </p>
          <p>
            The agent token, which the exchange returns, crosses in clear only if the exchange is
            made over plain HTTP, and the commands below do not make it so: they use{' '}
            <code>{SERVER_URL_PLACEHOLDER}</code>. Put the server&apos;s HTTPS address in its
            place. The agent refuses a plain-HTTP server address anyway, unless it is on the
            agent&apos;s own host.
          </p>
        </>
      ) : (
        <p>
          This page is served over plain HTTP, so the commands below use{' '}
          <code>{SERVER_URL_PLACEHOLDER}</code>: put the server&apos;s HTTPS address in its place.
          The agent refuses a plain-HTTP server address, unless it is on the agent&apos;s own host.
        </p>
      )}
    </div>
  )
}

// The commands are pinned to the server's version; without a release version to pin
// them to they carry a placeholder rather than a guessed artefact name
export function VersionNote({ serverVersion }) {
  if (releaseVersion(serverVersion)) return null
  return (
    <p className="mt-3 text-xs text-amber-200" data-testid="agent-version-unknown">
      {serverVersion
        ? `The server reports version ${serverVersion}, which is not a release version, so`
        : "The server's version is not known, so"}{' '}
      the commands below carry <code>{VERSION_PLACEHOLDER}</code>: put the release that matches
      the server in its place.
    </p>
  )
}

const CONTAINER_PREREQUISITES = [
  'A Linux host on the VLAN to be scanned',
  'Docker or Podman running as root: the agent needs host networking and CAP_NET_RAW, which rootless runtimes cannot grant',
]

const SYSTEMD_PREREQUISITES = [
  'A Linux host (or LXC) on the VLAN to be scanned, with systemd',
  'Python 3.11 or later, with venv (on Debian: apt install python3-venv)',
  'root, through sudo',
]

// What the owner does with an enrolment token the server has just issued, for a new
// agent or a re-enrolled one (spec §5.6): the token, shown once, and the two ways to run
// the agent with it. The token lives in the caller's state only.
export default function EnrolmentSteps({ token, expires, serverVersion, location, reEnrol }) {
  const { url, secure } = serverUrlFor(location)
  return (
    <>
      <div className="flex gap-2 rounded-md border border-amber-800 bg-amber-950/60 px-3 py-2 text-xs text-amber-200">
        <TriangleAlert size={14} className="mt-0.5 shrink-0" />
        <span>
          This is the only time the enrolment token is shown. It can be exchanged once, until{' '}
          {expires ? expires.toLocaleString() : 'an hour from now'}, for the agent&apos;s own
          token, which the agent keeps and this page never sees. Copy it or the command now: once
          this dialog closes it cannot be shown again. If it is lost or runs out, re-enrol the
          agent for a new one.
        </span>
      </div>
      <CopyBlock label="Enrolment token" text={token} testId="agent-token" />
      {!secure && <PlainHttpWarning issued />}
      <VersionNote serverVersion={serverVersion} />
      <InstallTabs>
        {(tab) =>
          tab === 'container' ? (
            <>
              <Prerequisites items={CONTAINER_PREREQUISITES} />
              {reEnrol && (
                <>
                  <p className="mt-3 text-xs text-slate-400">
                    If the host still runs this agent, remove its container and the volume that
                    holds its old token first: a stored token always wins over a new enrolment
                    token.
                  </p>
                  <CopyBlock
                    label="Remove the old agent"
                    text={containerForget()}
                    testId="agent-forget-command"
                  />
                </>
              )}
              <CopyBlock
                label="Container command"
                text={containerRun({ url, enrolToken: token, version: serverVersion })}
                testId="agent-command"
              />
              <p className="mt-3 text-xs text-slate-400">
                On its first start the agent exchanges the enrolment token for its own and keeps
                that in the <code>tlg-ipam-agent</code> volume, so the container can be updated
                without enrolling again. Add <code>-e ALLOWED_CIDRS=…</code> to narrow what it may
                scan (default: the private and CGNAT ranges), <code>-e ALLOWED_DNS_SERVERS=…</code>{' '}
                to limit the DNS servers its reverse lookups may ask (default: any), or{' '}
                <code>-e MAX_PPS=…</code> to change its rate limit (default 200 packets/s). For a
                server certificate from a private CA, mount the CA bundle and name it in{' '}
                <code>-e SSL_CERT_FILE=…</code>.
              </p>
            </>
          ) : (
            <>
              <Prerequisites items={SYSTEMD_PREREQUISITES} />
              {reEnrol && (
                <>
                  <p className="mt-3 text-xs text-slate-400">
                    If the host still has this agent installed, uninstall it first:{' '}
                    <code>install</code> refuses a new enrolment token while the old agent token
                    is stored.
                  </p>
                  <CopyBlock
                    label="Remove the old agent"
                    text={systemdForget()}
                    testId="agent-forget-command"
                  />
                </>
              )}
              <CopyBlock
                label="Install commands"
                text={systemdInstall({ url, version: serverVersion })}
                testId="agent-systemd-commands"
              />
              <p className="mt-3 text-xs text-slate-400">
                The last command asks for the enrolment token, without echoing it: paste the one
                above. It is never given as an argument, which other users of the host could read
                and sudo would log. <code>install</code> makes the exchange itself, keeps the
                agent&apos;s token for the service, and starts the <code>tlg-ipam-agent</code>{' '}
                unit. Add <code>--allowed-cidrs …</code>, <code>--max-pps …</code> or{' '}
                <code>--ca-file …</code> (a CA bundle for a server certificate from a private CA)
                to the last command to set them. To limit the DNS servers its reverse lookups may
                ask, set <code>ALLOWED_DNS_SERVERS</code> in a drop-in (
                <code>sudo systemctl edit tlg-ipam-agent</code>), since <code>install</code>{' '}
                rewrites its settings file each time it runs.
              </p>
            </>
          )
        }
      </InstallTabs>
    </>
  )
}
