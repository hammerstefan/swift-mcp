# Swift MCP and Workshop quickstart

Use Copilot CLI to check the object count and bytes used by one Swift
container. Swift MCP runs on your Linux host; Copilot runs in the included
`swift-mcp-quickstart` Workshop. You don't need to install Copilot CLI or
create a Copilot token before you start. Run the commands from the **repository
root**, in your own desktop session. Only package installation needs `sudo`.

For a first run with an OpenStack RC file, use the [one-command quickstart](#one-command-quickstart).
If you supply Swift credentials through environment variables or want to run
the server and Copilot separately, use the [two-terminal setup](#manual-two-terminal-alternative).
Both use the same Workshop project, `quickstart/`. Run only one at a time.

## Before you start

- **Swift:** Use a [dedicated read-only identity](../README.md#read-only-swift-deployment) when possible
  and a container whose name and metadata you can share with Copilot's
  model provider. For the wizard, keep the identity's [OpenStack RC file](../README.md#openstack-rc-file)
  outside the checkout, owned by you and accessible only to you (for example,
  mode `0600`).
- **Copilot:** Use a GitHub account with Copilot CLI access. The wizard needs
  direct HTTPS access to `api.github.com` to check your token; that check does
  not use proxy or custom CA environment settings.
- **Host:** Use Linux with Python 3.11+, Bash 5.1+, `just`, `uv`, `flock`, an
  unlocked user-session Secret Service keyring, and Workshop 0.9.8+ with LXD
  6.8+. The two-terminal setup also uses `secret-tool`. Port `127.0.0.1:8000`
  must be free. Use the included container Workshop; VM Workshops do not
  support the tunnel.

### Set up an Ubuntu 24.04 host

If you already have the tools and an unlocked keyring, skip to the
[wizard](#one-command-quickstart) or the [two-terminal setup](#manual-two-terminal-alternative).
On Ubuntu 24.04 LTS, install the host tools:

```sh
sudo apt update
sudo apt install python3 bash just util-linux libsecret-tools pipx
pipx install uv
pipx ensurepath
```

Open a new terminal so `uv` is on your `PATH`. On other Linux distributions,
see the [uv](https://docs.astral.sh/uv/getting-started/installation/) and
[just](https://just.systems/man/en/installation.html) installation guides.

For a new LXD and Workshop installation, run:

```sh
sudo snap install --channel=6/stable lxd
sudo snap install --classic workshop
workshop --version
```

If either snap is already installed, skip its install command. See the
[LXD requirement](https://ubuntu.com/workshop/docs/tutorial/part-1-get-started.md#tut-install)
and [Workshop upgrade instructions](https://ubuntu.com/workshop/docs/release-notes.md#release-upgrade)
if an installed version is too old.

On Ubuntu Desktop, open **Passwords and Keys** (install it with
`sudo apt install seahorse` if needed). Under **Passwords**, right-click your
default keyring, usually **Login**, and select **Unlock**. If prompted, enter
the keyring password in the dialog.

## One-command quickstart

The wizard launches `swift-mcp-quickstart` on first use and reuses the stopped
Workshop on later runs. If it is running, finish your work and run
`workshop -p quickstart stop` before starting the wizard. If the wizard
reports an existing tunnel or token connection, inspect it with
`workshop -p quickstart connections`. Leave it alone if you cannot identify
who is using it.

Have your RC file path and container name ready. The wizard prompts for
both and uses your answers for this run only. To skip those prompts, set the
variables **before** launching it. Replace both examples with your own values:

```sh
export SWIFT_MCP_OPENRC="$HOME/test-swift.rc"
export SWIFT_MCP_CONTAINERS='["my-container"]'
```

Start the wizard:

```sh
just quickstart
```

1. Confirm the session. If prompted for containers, enter one name, a
   comma-separated list, or a JSON array.
2. The wizard checks your saved Copilot token with GitHub. If there is no
   token, it opens the token form; if GitHub rejects the token, it offers to
   open the form for a replacement. Follow the instructions and paste the new
   token only at the hidden prompt. The wizard checks it before saving it in
   your keyring. This checks GitHub authentication, not Copilot permission,
   subscription, or model availability.
3. The wizard creates or reuses the Workshop's MCP caller key, starts the
   server on the host, and opens Copilot. No key ID is needed. Verify the call
   below.

## Verify one Swift tool call

1. In Copilot, open `/mcp` and check that `swift-mcp` is listed.
2. Enter this prompt, replacing `my-container` with your container:

   > Use `get_container_stats` from `swift-mcp` for `my-container`.
   > Show the object count and bytes used. Do not read any objects.

3. If Copilot asks for approval, approve only that call. Open the **tool
   result** and look for `object_count` and `bytes_used`. Those fields confirm
   that Swift returned container metadata, even if both values are zero.
   This quickstart sets an allowlist, so `list_containers` can
   show a configured name even if Swift cannot read that container.

**If you used the wizard:** Exit Copilot. The wizard disconnects the token and
tunnel, stops Swift MCP and the Workshop, and removes the temporary server
environment. It prints the path to your saved project. The Copilot token and
[caller-key credentials](key-management.md) remain available for the next run.
If the token or tunnel stays connected, or the Workshop doesn't stop, follow
the [Workshop recovery steps](#recover-an-interrupted-wizard-run).

**If you used two terminals:** Follow [Stop and disconnect](#stop-and-disconnect).

## Manual two-terminal alternative

Open two host terminals in the repository root, in the same keyring session.
The server runs in terminal 1. Use terminal 2 for Copilot.

### 1. Store the Copilot token

If the quickstart token is already in your keyring and still valid, skip to
step 2. Otherwise, open the [prefilled GitHub token form](https://github.com/settings/personal-access-tokens/new?name=Swift%20MCP%20quickstart&expires_in=7&user_copilot_requests=read)
and check these settings before generating the token:

- **Resource owner:** your personal account
- **Expiration:** seven days
- **Repository access:** Public repositories
- **Permissions → Account:** Copilot Requests, Read. Add no other permissions.

In **terminal 1**, run this command and paste the token only at the prompt:

```sh
secret-tool store --label="Swift MCP quickstart Copilot" --collection=default \
  service copilot-cli username swift-mcp-quickstart
```

Keep the `service` and `username` values as shown: the [Workshop definition](../quickstart/workshop.yaml)
uses them to find exactly one keyring item. Never paste the token into YAML,
a shell command, or Copilot chat. See [GitHub's authentication instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli#authenticating-with-environment-variables).

### 2. Start the server and connect the Workshop

In **terminal 1**, run:

```sh
uv sync --locked
```

In that terminal, choose how to supply Swift credentials:

- **RC file:** Swift MCP reads assignments without running shell commands.
  Set the path to your file:

  ```sh
  export SWIFT_MCP_OPENRC='/path/to/test-swift.rc'
  ```

- **Environment:** Leave `SWIFT_MCP_OPENRC` unset. Follow the
  [Keystone v3 password setup](../README.md#keystone-v3-password) or
  [pre-issued token setup](../README.md#pre-issued-token) in this terminal.

Replace `my-container` with the container you will verify. This manual
recipe takes a **JSON array**, even for one container. Then start the server:

```sh
export SWIFT_MCP_CONTAINERS='["my-container"]'
just quickstart-serve
```

The recipe creates or reuses the Workshop's **MCP caller key** and prints its
numeric **ID**. The key is stored in your host keyring. Copilot uses this key
to authenticate to Swift MCP and the separate Copilot token to authenticate
to GitHub. If key setup fails, see [key management](key-management.md) before
changing any stored keys.

The recipe starts Swift MCP on `127.0.0.1:8000`, starts or launches the
Workshop if needed, and connects the tunnel and token. **Note the printed
caller key ID and leave terminal 1 open.** Wait for the prompt to run Copilot.

### 3. Start Copilot in the Workshop

In **terminal 2**, replace `42` with the caller key ID from terminal 1:

```sh
KEY_ID=42 just quickstart-copilot
```

The recipe retrieves the key without displaying it and passes it to Copilot
for this run only. [Verify one Swift tool call](#verify-one-swift-tool-call).

### Stop and disconnect

1. Exit Copilot in terminal 2.
2. Press `Ctrl+C` in terminal 1 to stop Swift MCP and disconnect the token
   and tunnel.
3. In terminal 2, run `workshop -p quickstart stop`. This keeps the Workshop
   for your next run.

## Troubleshooting

- **Workshop launch or connection failed:** Follow the [Workshop recovery steps](#recover-an-interrupted-wizard-run)
  to inspect the failed change. Disconnect only connections you recognize from
  the interrupted run.
- **Copilot asks you to sign in:** Run `workshop -p quickstart connections` and
  check the `github-token` connection. Check the token's keyring labels,
  expiration, and **Copilot Requests** permission.
- **Wizard cannot check the Copilot token:** Check direct HTTPS connectivity to
  `api.github.com`. If GitHub reports a permission or rate-limit problem,
  check the token permissions or retry later. The wizard does not replace a
  saved token when the check is inconclusive.
- **Swift MCP returns HTTP 401:** Check the MCP caller key ID. In the
  two-terminal setup, `KEY_ID` must match the ID printed in terminal 1.
- **Caller-key setup fails:** Run `uv run --locked swift-mcp-keys status swift-mcp-quickstart`.
  The wizard and `quickstart-serve` stop if there is a pending key or more than one active key.
  Follow [key recovery](key-management.md#recover-an-interrupted-key-operation)
  or [finish the rotation](key-management.md#routine-rotation) before retrying.

Do not print either secret while troubleshooting. See [more troubleshooting](../README.md#troubleshooting).

### Access files from a wizard run

Files saved in `/project` are already on the host in `quickstart/`. To access
files elsewhere in the stopped Workshop, start it and open a shell:

```sh
workshop -p quickstart start
workshop -p quickstart shell
```

Exit the shell and run `workshop -p quickstart stop` before the next guided
run. Stopping keeps container files; `workshop remove` deletes them. The
wizard does not overwrite project files or refresh a changed Workshop
definition. Files in `/project` may appear as untracked files in your checkout.
Keep credentials out of the project and do not commit sensitive files.

### Recover an interrupted wizard run

Use these steps if the Workshop failed to start, disconnect, or stop. Run the
commands from the repository root.

1. Check its connections and recent changes:

   ```sh
   workshop -p quickstart connections
   workshop -p quickstart changes
   ```

2. If a change failed, inspect it. Replace `42` with the ID from `changes`:

   ```sh
   workshop -p quickstart tasks 42
   ```

3. If the quickstart token or tunnel is still connected, disconnect only the
   connections still listed for this run:

   ```sh
   workshop -p quickstart disconnect swift-mcp-quickstart/copilot:github-token
   workshop -p quickstart disconnect swift-mcp-quickstart/copilot:swift-mcp
   ```

4. If the Workshop from your run is still running, run `workshop -p quickstart stop`.
   This preserves files in both the project and the container.

Older wizard versions saved separate projects under
`~/.local/share/swift-mcp/quickstarts/`. This wizard leaves those projects
untouched. To access one, pass its saved project path to `-p` instead of `quickstart`.

## Security notes

The tunnel does not authenticate callers. Any process in the Workshop can
request the connected Copilot token. Copilot and its child processes can
access the MCP caller key during the run. Keep approval prompts enabled and
keep Swift credentials on the host, outside the checkout and logs.

If this was a one-time test, revoke the [GitHub token](https://github.com/settings/personal-access-tokens)
and [revoke the MCP caller key](key-management.md#revoke-a-caller-key) for
`swift-mcp-quickstart` when finished.
