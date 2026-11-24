import { containerUpdate, systemdUpdate } from '../utils/install'
import CopyBlock from './CopyBlock'
import { PlainHttpWarning, serverUrlFor, VersionNote } from './EnrolmentSteps'
import InstallTabs from './InstallTabs'
import Modal, { primaryBtn } from './Modal'

// The update command for an agent flagged older than the server, or refused for its
// protocol (spec §5.6, "Version reporting"). Updates are the operator's job: the page
// shows the commands, pinned to the server's version, and nothing else changes. No
// token is involved: the agent keeps the one it holds.
export default function UpdateModal({ agent, serverVersion, onClose, location = window.location }) {
  const { url, secure } = serverUrlFor(location)
  return (
    <Modal title={`Update ${agent.name}`} onClose={onClose} maxWidth="max-w-3xl">
      <p className="text-sm text-slate-300">
        {agent.protocol_mismatch
          ? `${agent.name} speaks another protocol than this server, so it is given no scans. Install the release that matches the server`
          : `${agent.name} runs ${agent.version}, older than this server${serverVersion ? ` (${serverVersion})` : ''}. Install the release that matches it`}
        , on the agent&apos;s host, the way it was installed.
      </p>
      {!secure && <PlainHttpWarning />}
      <VersionNote serverVersion={serverVersion} />
      <InstallTabs>
        {(tab) =>
          tab === 'container' ? (
            <>
              <CopyBlock
                label="Update commands"
                text={containerUpdate({ url, version: serverVersion })}
                testId="agent-update-command"
              />
              <p className="mt-3 text-xs text-slate-400">
                The new container keeps the agent token in the <code>tlg-ipam-agent</code> volume,
                so it does not enrol again. Give it the same <code>-e</code> settings the old one
                had.
              </p>
            </>
          ) : (
            <>
              <CopyBlock
                label="Update commands"
                text={systemdUpdate({ url, version: serverVersion })}
                testId="agent-update-command"
              />
              <p className="mt-3 text-xs text-slate-400">
                <code>install</code> without <code>--enrol-token</code> keeps the stored agent token
                and rewrites the settings and the unit: give it the same{' '}
                <code>--allowed-cidrs</code>, <code>--max-pps</code> and <code>--ca-file</code> it
                was installed with.
              </p>
            </>
          )
        }
      </InstallTabs>
      <div className="mt-5 flex justify-end">
        <button onClick={onClose} className={primaryBtn}>
          Done
        </button>
      </div>
    </Modal>
  )
}
