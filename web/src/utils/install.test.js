// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  AGENT_IMAGE,
  agentServerUrl,
  containerForget,
  containerRun,
  containerUpdate,
  releaseVersion,
  shellQuote,
  systemdForget,
  systemdInstall,
  systemdUpdate,
  wheelUrl,
} from './install'

// The spec is the contract for these commands (spec §5.6, "Guided install (M2b)" and
// "Updating and re-enrolling a host"): each rendered command is matched against the
// shape the spec's tables give, read from the spec itself, so a change on either side
// that the other does not follow fails here.
const SPEC = readFileSync(new URL('../../../docs/v0.2-spec.md', import.meta.url), 'utf8')

// The backticked spans of the table row whose first cell is `tab`, in the table that
// follows `heading`
function row(heading, tab) {
  const start = SPEC.indexOf(heading)
  expect(start).toBeGreaterThan(-1)
  const line = SPEC.slice(start)
    .split('\n')
    .find((l) => l.startsWith(`| ${tab} |`))
  expect(line).toBeDefined()
  return line
    .split('|')
    .slice(2, -1)
    .map((cell) => [...cell.matchAll(/`([^`]+)`/g)].map((m) => m[1]))
}

// A shape as a pattern: `…` and the `<placeholders>` stand for one word each
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
function shape(text) {
  return new RegExp(`^${escape(text).replace(/…|<[^>]+>/g, '\\S+')}$`)
}

// One command as one line: the rendered continuations joined
const oneLine = (command) => command.replace(/ \\\n\s+/g, ' ')

const URL_ = 'https://ipam.home.arpa'
const ENROL = 'tlge_ENROLenrolENROLenrolENROLenrolENROL0123'
const VERSION = '0.2.0'

const INSTALL = 'Guided install (M2b)'
const LATER = 'Updating and re-enrolling a host'

describe('the install commands match the spec', () => {
  it('container: the docker run of the spec, with the enrolment token', () => {
    const [prerequisites, [command]] = row(INSTALL, 'Container')
    expect(prerequisites.length).toBe(0)
    const rendered = containerRun({ url: URL_, enrolToken: ENROL, version: VERSION })
    expect(oneLine(rendered)).toMatch(shape(command))
    expect(rendered).toContain(`-e IPAM_ENROL_TOKEN=${ENROL}`)
    expect(rendered).toContain(`-e IPAM_URL=${URL_}`)
    expect(rendered.endsWith(`${AGENT_IMAGE}:${VERSION}`)).toBe(true)
  })

  it('systemd: the three commands of the spec, in order, the token never an argument', () => {
    const [, commands] = row(INSTALL, 'systemd')
    // the cell's spans: three commands, then the token it asks for
    const shapes = commands.slice(0, 3)
    expect(commands[3]).toBe('tlge_…')
    const lines = systemdInstall({ url: URL_, version: VERSION }).split('\n')
    expect(lines.length).toBe(shapes.length)
    lines.forEach((line, i) => expect(line).toMatch(shape(shapes[i])))
    expect(lines[1]).toContain(wheelUrl(VERSION))
    expect(lines[2]).toContain(`--url ${URL_} --enrol-token -`)
  })

  it('container update: pull, remove, then the run without the enrolment token', () => {
    const [[pull, remove, left]] = row(LATER, 'Container')
    const lines = containerUpdate({ url: URL_, version: VERSION }).split('\n')
    expect(lines[0]).toMatch(shape(pull))
    expect(lines[1]).toMatch(shape(remove))
    const [, [install]] = row(INSTALL, 'Container')
    // the option the spec says is left out, by its name: `-e IPAM_ENROL_TOKEN`
    const [option] = left.split('=')
    expect(option).toBe('-e IPAM_ENROL_TOKEN')
    const withoutToken = install.replace(new RegExp(` ${escape(option)}=\\S+`), '')
    expect(withoutToken).not.toBe(install)
    expect(oneLine(lines.slice(2).join('\n'))).toMatch(shape(withoutToken))
  })

  it('systemd update: the wheel, then install without --enrol-token', () => {
    const [[pip, install]] = row(LATER, 'systemd')
    const lines = systemdUpdate({ url: URL_, version: VERSION }).split('\n')
    expect(lines.length).toBe(2)
    expect(lines[0]).toMatch(shape(pip))
    expect(lines[1]).toMatch(shape(install))
    expect(lines[1]).not.toContain('--enrol-token')
  })

  it('re-enrol: what removes the stored token first', () => {
    const [, container] = row(LATER, 'Container')
    expect(containerForget().split('\n')).toEqual(container)
    const [, systemd] = row(LATER, 'systemd')
    expect([systemdForget()]).toEqual(systemd)
  })

  it('no command holds an agent token, and only the container install the enrolment token', () => {
    const commands = {
      run: containerRun({ url: URL_, enrolToken: ENROL, version: VERSION }),
      systemd: systemdInstall({ url: URL_, version: VERSION }),
      containerUpdate: containerUpdate({ url: URL_, version: VERSION }),
      systemdUpdate: systemdUpdate({ url: URL_, version: VERSION }),
      containerForget: containerForget(),
      systemdForget: systemdForget(),
    }
    for (const [name, text] of Object.entries(commands)) {
      expect(text, name).not.toContain('tlga_')
      expect(text, name).not.toContain('IPAM_AGENT_TOKEN')
      if (name !== 'run') expect(text, name).not.toContain('tlge_')
    }
  })

  it('names the artefacts where the spec says the release publishes them', () => {
    expect(SPEC).toContain(
      `\`<registry>\` is \`${AGENT_IMAGE.replace('/tlg-ipam-agent', '')}\``
    )
    expect(SPEC).toContain(
      `the release wheel URL is \`${wheelUrl('<version>')}\``
    )
    expect(wheelUrl('0.2.0')).toBe(
      'https://github.com/DeusMaximus/TLG-IPAM/releases/download/v0.2.0/tlg_ipam_agent-0.2.0-py3-none-any.whl'
    )
  })
})

describe('the version the commands are pinned to', () => {
  it('is the server version when it is a release version in normal form', () => {
    for (const v of ['0.2.0', '1.0', '0.2.0rc1', '0.2.0.dev0', '0.2.0.post1']) {
      expect(releaseVersion(v)).toBe(v)
    }
  })

  it('is a placeholder otherwise, never a guessed artefact name', () => {
    for (const v of [undefined, null, '', 'v0.2.0', '0.2.0+local', '0.2.0-rc1', '1!2.0', '0.2 ']) {
      expect(releaseVersion(v)).toBeNull()
    }
    const run = containerRun({ url: URL_, enrolToken: ENROL, version: '0.2.0+local' })
    expect(run.endsWith(`${AGENT_IMAGE}:<version>`)).toBe(true)
    expect(systemdInstall({ url: URL_, version: undefined })).toContain(
      "'https://github.com/DeusMaximus/TLG-IPAM/releases/download/v<version>/tlg_ipam_agent-<version>-py3-none-any.whl'"
    )
  })
})

describe('shellQuote', () => {
  it('quotes anything with shell metacharacters', () => {
    expect(shellQuote('plain-1.2')).toBe('plain-1.2')
    expect(shellQuote("a b'c")).toBe(`'a b'\\''c'`)
    expect(shellQuote('$(id)')).toBe(`'$(id)'`)
  })

  it('quotes a server URL that is not plain', () => {
    expect(systemdInstall({ url: 'https://<server>', version: VERSION })).toContain(
      "--url 'https://<server>' --enrol-token -"
    )
  })
})

describe('agentServerUrl', () => {
  const at = (url) => {
    const u = new URL(url)
    return agentServerUrl({ protocol: u.protocol, hostname: u.hostname, origin: u.origin })
  }

  it('gives an https origin, or a plain-http one only on a loopback host', () => {
    expect(at('https://ipam.home.arpa')).toBe('https://ipam.home.arpa')
    expect(at('https://10.0.0.2:8443')).toBe('https://10.0.0.2:8443')
    expect(at('http://localhost:8080')).toBe('http://localhost:8080')
    expect(at('http://127.0.0.1:8080')).toBe('http://127.0.0.1:8080')
    expect(at('http://[::1]:8080')).toBe('http://[::1]:8080')
  })

  it('gives nothing for plain http across a network', () => {
    expect(at('http://ipam.home.arpa')).toBeNull()
    expect(at('http://10.0.0.2:8080')).toBeNull()
    expect(at('http://127.0.0.1.example.com')).toBeNull()
  })
})
