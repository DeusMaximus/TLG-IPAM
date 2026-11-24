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
// sends a token in clear. `location` is the page's own (`window.location`).
export function serverUrlFor(location) {
  const secure = agentServerUrl(location)
  return { url: secure ?? SERVER_URL_PLACEHOLDER, secure: Boolean(secure) }
}

export function PlainHttpWarning() {
  return (
    <p className="mt-3 text-xs text-amber-200" data-testid="agent-needs-https">
      This page is served over plain HTTP, which would carry the enrolment token across the
      network in clear. The commands below use <code>{SERVER_URL_PLACEHOLDER}</code>: put the
      server&apos;s HTTPS address in its place. The agent refuses a plain-HTTP server address
      anyway, unless it is on the agent&apos;s own host.
    </p>
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
      {!secure && <PlainHttpWarning />}
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
                scan (default: the private and CGNAT ranges) or <code>-e MAX_PPS=…</code> to
                change its rate limit (default 200 packets/s). For a server certificate from a
                private CA, mount the CA bundle and name it in <code>-e SSL_CERT_FILE=…</code>.
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
                to the last command to set them.
              </p>
            </>
          )
        }
      </InstallTabs>
    </>
  )
}
