// The guided install's commands (spec §5.6, "Guided install (M2b)"). The page renders
// them; the server serves no code. The image comes from the project's container
// registry and the wheel from its GitHub release, both pinned to the server's own
// version (`GET /health`), which the release pipeline (#24) makes the agent's too.
// `install.test.js` holds every command to the shapes the spec gives.
//
// No command holds the long-lived agent token (`tlga_…`), which the page never sees.
// The one-hour, single-use enrolment token (`tlge_…`) is in the container command
// only, as `IPAM_ENROL_TOKEN`; the systemd install reads it from standard input.

export const AGENT_IMAGE = 'ghcr.io/deusmaximus/tlg-ipam-agent'
export const RELEASE_DOWNLOADS = 'https://github.com/DeusMaximus/TLG-IPAM/releases/download'
export const CONTAINER_NAME = 'tlg-ipam-agent'
// the named volume that keeps the agent token across container updates
export const STATE_VOLUME = 'tlg-ipam-agent'
export const STATE_DIR = '/var/lib/tlg-ipam-agent'
export const VENV = '/opt/tlg-ipam-agent'
export const VERSION_PLACEHOLDER = '<version>'
export const SERVER_URL_PLACEHOLDER = 'https://<server>'

// Leave a value bare when it has no shell metacharacters; single-quote it otherwise
export function shellQuote(value) {
  const s = String(value)
  return /^[A-Za-z0-9_./:=@%+-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`
}

// The server URL an agent may be given (the agent's own `config.parse_url` rule):
// `https`, or plain `http` only to a loopback host, where nothing crosses a network.
// A page served over plain `http` from any other host gets null, so no command built
// from it sends a token in clear.
const LOOPBACK_HOST = /^(localhost|127(\.\d{1,3}){3}|\[::1\])$/i
export function agentServerUrl({ protocol, hostname, origin }) {
  if (protocol === 'https:') return origin
  if (protocol === 'http:' && LOOPBACK_HOST.test(hostname)) return origin
  return null
}

// A version the artefacts can be pinned to: a PEP 440 public version in its normal
// form (`0.2.0`, `0.2.0rc1`, `0.2.0.dev0`), which is the wheel's file name as built and
// a valid image tag. Anything else (no version yet, a local `+…` build, a spelling PEP
// 440 normalises to another, such as `00.02.00` for `0.2.0` or `0.2.0rc01` for
// `0.2.0rc1`) gets null, and the commands carry VERSION_PLACEHOLDER instead of guessing
// an artefact name. Each number is written without leading zeroes, as normalising
// writes it. The release check (`.github/scripts/release_check.py`) refuses the same
// versions: `agent/tests/test_release.py` gives it the cases `install.test.js` gives
// this, which reads them from there.
const N = '(0|[1-9]\\d*)'
const RELEASE_VERSION = new RegExp(
  `^${N}(\\.${N})*((a|b|rc)${N})?(\\.post${N})?(\\.dev${N})?$`
)
export const releaseVersion = (version) =>
  typeof version === 'string' && RELEASE_VERSION.test(version) ? version : null

const pinned = (version) => releaseVersion(version) ?? VERSION_PLACEHOLDER

export const imageRef = (version) => `${AGENT_IMAGE}:${pinned(version)}`

// The release's wheel, as flit names it: `tlg_ipam_agent-<version>-py3-none-any.whl`,
// attached to the release tagged `v<version>`
export function wheelUrl(version) {
  const v = pinned(version)
  return `${RELEASE_DOWNLOADS}/v${v}/tlg_ipam_agent-${v}-py3-none-any.whl`
}

// `docker run` of the agent: host networking and CAP_NET_RAW, the state volume, the
// server URL and, on a first start, the enrolment token. Without one (an update) the
// agent uses the token stored in the volume. ALLOWED_CIDRS and MAX_PPS keep their
// defaults.
export function containerRun({ url, enrolToken, version }) {
  return [
    `docker run -d --name ${CONTAINER_NAME} --restart unless-stopped \\`,
    '  --network host --cap-add NET_RAW \\',
    `  -v ${STATE_VOLUME}:${STATE_DIR} \\`,
    `  -e IPAM_URL=${shellQuote(url)} \\`,
    ...(enrolToken ? [`  -e IPAM_ENROL_TOKEN=${shellQuote(enrolToken)} \\`] : []),
    `  ${imageRef(version)}`,
  ].join('\n')
}

// The systemd install's three commands, one per line. The last asks for the enrolment
// token without echoing it (or reads it from a pipe): the token is never an argument.
export function systemdInstall({ url, version }) {
  return [
    `sudo python3 -m venv ${VENV}`,
    `sudo ${VENV}/bin/pip install ${shellQuote(wheelUrl(version))}`,
    `sudo ${VENV}/bin/tlg-ipam-agent install --url ${shellQuote(url)} --enrol-token -`,
  ].join('\n')
}

// Updating an agent flagged older than the server: the operator's job (spec §5.6).
// Container: pull the image, replace the container; the volume keeps its token.
export function containerUpdate({ url, version }) {
  return [
    `docker pull ${imageRef(version)}`,
    `docker rm -f ${CONTAINER_NAME}`,
    containerRun({ url, version }),
  ].join('\n')
}

// systemd: install the release into the same environment and run `install` again
// without `--enrol-token`, which keeps the stored token and rewrites the settings and
// the unit (so the flags it was installed with must be given again)
export function systemdUpdate({ url, version }) {
  return [
    `sudo ${VENV}/bin/pip install ${shellQuote(wheelUrl(version))}`,
    `sudo ${VENV}/bin/tlg-ipam-agent install --url ${shellQuote(url)}`,
  ].join('\n')
}

// Re-enrolling a host that still holds the agent's old token: a stored token always
// wins over a new enrolment token, and `install` refuses `--enrol-token` while one is
// stored, so it is removed first
export const containerForget = () =>
  [`docker rm -f ${CONTAINER_NAME}`, `docker volume rm ${STATE_VOLUME}`].join('\n')
export const systemdForget = () => `sudo ${VENV}/bin/tlg-ipam-agent uninstall`
