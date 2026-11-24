// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  AGENT_IMAGE,
  RELEASE_DOWNLOADS,
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

// The release check's cases (`agent/tests/test_release.py`), read from that file so the
// page and the release pipeline judge one list: what the check takes, and what it
// refuses (#143). The read is strict: every line of the two parametrize blocks is a
// shape it knows (a case, a comment, or the block's own syntax), so a case written in
// any other way (single quotes, a tuple over several lines) fails here instead of
// being left out of one side.
const STRING = String.raw`"((?:[^"\\]|\\.)*)"`
const TAKEN_LIST = new RegExp(String.raw`^\[(${STRING}(?:, ${STRING})*)\],$`)
const REFUSED_CASE = new RegExp(String.raw`^\(${STRING}, "[^"\\]*"\),$`)
const unquote = (text) => [...text.matchAll(new RegExp(STRING, 'g'))].map((m) => m[1])

function parseReleaseCases(source) {
  const block = (before, header) => {
    const end = source.indexOf(before)
    if (end < 0) throw new Error(`no ${before} in test_release.py`)
    const start = source.lastIndexOf('@pytest.mark.parametrize(', end)
    const lines = source.slice(start, end).split('\n').map((l) => l.trim())
    if (lines.shift() !== '@pytest.mark.parametrize(' || lines.shift() !== header) {
      throw new Error(`${before}: the parametrize block does not start as expected`)
    }
    return lines.filter((l) => l && !l.startsWith('#') && l !== ')')
  }
  const taken = block('def test_a_release_version(', '"version",').flatMap((line) => {
    const m = TAKEN_LIST.exec(line)
    if (!m) throw new Error(`test_a_release_version: a line this read does not know: ${line}`)
    return unquote(m[1])
  })
  const refusedLines = block('def test_not_a_release_version(', '"version, reason",')
  if (refusedLines.shift() !== '[' || refusedLines.pop() !== '],') {
    throw new Error('test_not_a_release_version: the case list is not one [ … ] block')
  }
  const refused = refusedLines.map((line) => {
    const m = REFUSED_CASE.exec(line)
    if (!m) throw new Error(`test_not_a_release_version: a line this read does not know: ${line}`)
    return m[1]
  })
  return { taken, refused }
}

const releaseCases = () => parseReleaseCases(read('agent/tests/test_release.py'))

describe('the version the commands are pinned to', () => {
  it('reads the release check’s cases whole', () => {
    const { taken, refused } = releaseCases()
    expect(taken).toEqual(expect.arrayContaining(['0.2.0', '0.2.0rc1', '0.2.0.dev0', '0.2.0.post1']))
    expect(refused).toEqual(
      expect.arrayContaining(['', '0.2.0+local', '1!2.0', '0.2 ', '01.2', '00.02.00', '0.2.0rc01'])
    )
  })

  it('refuses a case written in a shape it does not read, rather than skip it', () => {
    const source = read('agent/tests/test_release.py')
    const variants = [
      // single quotes
      source.replace('("00.02.00", "not in normal form"),', "('00.02.00', 'not in normal form'),"),
      // a tuple over two lines
      source.replace('("00.02.00", "not in normal form"),', '(\n        "00.02.00",\n        "not in normal form",\n    ),'),
      // a second list line, or one with a single-quoted case
      source.replace('"0.2.0.post1"],', "'0.2.0.post1'],"),
      source.replace('["0.2.0", "1.0",', '["0.2.0",\n     "1.0",'),
    ]
    for (const variant of variants) {
      expect(variant).not.toBe(source)
      expect(() => parseReleaseCases(variant)).toThrow()
    }
  })

  it('is the server version when it is a release version in normal form', () => {
    for (const v of releaseCases().taken) {
      expect(releaseVersion(v)).toBe(v)
    }
  })

  it('is a placeholder otherwise, never a guessed artefact name', () => {
    // a spelling PEP 440 normalises to another, `00.02.00` (`0.2.0`) or `0.2.0rc01`
    // (`0.2.0rc1`), names no published artefact either
    for (const v of [undefined, null, ...releaseCases().refused]) {
      expect(releaseVersion(v)).toBeNull()
    }
    for (const v of ['00.02.00', '0.2.0rc01']) {
      expect(containerRun({ url: URL_, enrolToken: ENROL, version: v }).endsWith(`${AGENT_IMAGE}:<version>`)).toBe(true)
      expect(wheelUrl(v)).toBe(wheelUrl('<version>'))
    }
    const run = containerRun({ url: URL_, enrolToken: ENROL, version: '0.2.0+local' })
    expect(run.endsWith(`${AGENT_IMAGE}:<version>`)).toBe(true)
    expect(systemdInstall({ url: URL_, version: undefined })).toContain(
      "'https://github.com/DeusMaximus/TLG-IPAM/releases/download/v<version>/tlg_ipam_agent-<version>-py3-none-any.whl'"
    )
  })
})

// The release pipeline (#24) publishes what these commands name, at the one version the
// server and the agent share: its workflow names the image and the downloads as this
// module does, and both version literals are the same release version
const REPO = new URL('../../../', import.meta.url)
const read = (path) => readFileSync(new URL(path, REPO), 'utf8')

describe('the release publishes what the commands name', () => {
  const WORKFLOW = read('.github/workflows/release.yml')

  it('names the image and the release downloads as the commands do', () => {
    expect(WORKFLOW).toMatch(new RegExp(`^  IMAGE: ${escape(AGENT_IMAGE)}$`, 'm'))
    expect(WORKFLOW).toMatch(new RegExp(`^  RELEASES: ${escape(RELEASE_DOWNLOADS)}$`, 'm'))
  })

  it('attaches the wheel under the name the URL ends with, to the release v<version>', () => {
    const name = 'tlg_ipam_agent-$VERSION-py3-none-any.whl'
    expect(wheelUrl('9.8.7').endsWith('/v9.8.7/' + name.replace('$VERSION', '9.8.7'))).toBe(true)
    // the release job's name and tag, and the verify job's download
    expect(WORKFLOW).toContain(`name="${name}"`)
    expect(WORKFLOW).toContain('tag="v$VERSION"')
    expect(WORKFLOW).toContain('url="$RELEASES/v$VERSION/$name"')
    // the image under the bare version, which imageRef renders
    expect(WORKFLOW).toContain('ref="docker://$IMAGE:$VERSION"')
  })

  it('is at the one version of the server and the agent, which the page pins to', () => {
    const literal = (path, name) => {
      const found = [...read(path).matchAll(new RegExp(`^${name} = "([^"]*)"$`, 'gm'))]
      expect(found.length).toBe(1)
      return found[0][1]
    }
    const server = literal('api/app/version.py', 'VERSION')
    expect(literal('agent/src/tlg_ipam_agent/__init__.py', '__version__')).toBe(server)
    expect(releaseVersion(server)).toBe(server)
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
