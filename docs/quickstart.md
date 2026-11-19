# Swift MCP and Workshop quickstart

Use Copilot CLI to check the object count and bytes used by one Swift test
container. Swift MCP runs on your Linux host; Copilot runs in the included
`swift-mcp-quickstart` Workshop. You don't need to install Copilot CLI or
create a Copilot token before you start.

Use the [one-command quickstart](#one-command-quickstart) for a guided first
run. Use the [two-terminal alternative](#manual-two-terminal-alternative) to
start the server and Copilot separately with the project in `quickstart/`.

## Before you start

- **Host:** On Ubuntu 24.04 LTS, install Python, Bash, `just`, and `secret-tool`
  (used by the two-terminal setup). Install `uv` for your user with `pipx`:

  ```sh
  sudo apt update
  sudo apt install python3 bash just libsecret-tools pipx
  pipx install uv
  pipx ensurepath
  ```

  Open a new terminal so `uv` is on your `PATH`. The wizard also requires
  `flock`, provided by `util-linux` on Ubuntu. On other Linux distributions,
  use your package manager for Python 3.11 or later, Bash 5.1 or later, and
  `flock`; then
  see the [uv](https://docs.astral.sh/uv/getting-started/installation/) and
  [just](https://just.systems/man/en/installation.html) installation guides.

  Install Workshop and its LXD dependency. If LXD is already installed, skip
  the first command and follow the [Workshop installation guide](https://ubuntu.com/workshop/docs/tutorial/part-1-get-started.md#tut-install)
  if you need to update to LXD 6.8 or later.

  ```sh
  sudo snap install --channel=6/stable lxd
  sudo snap install --classic workshop
  workshop --version
  ```

  Check that Workshop reports 0.9.8 or later. For an existing installation, see
  the [Workshop upgrade instructions](https://ubuntu.com/workshop/docs/release-notes.md#release-upgrade).
  Use the included container Workshop; Workshop VMs do not support tunnels.
  Port `8000` must be free on `127.0.0.1`.
- **Keyring:** Unlock the default collection in your host's Secret Service
  keyring before running the quickstart. On Ubuntu Desktop with GNOME Keyring:

  1. Open **Passwords and Keys** (Seahorse). If it is not installed, run
     `sudo apt install seahorse`.
  2. Under **Passwords**, right-click your default keyring (usually **Login**)
     and select **Unlock** if it is locked.
  3. If prompted, enter the keyring password in the dialog and select **Unlock**.

  Run the quickstart commands from the repository root in a terminal in the
  same desktop session, as the user who owns the keyring. Do not use `sudo`
  to run the quickstart.
- **Copilot:** Use a GitHub account with access to Copilot CLI.
- **Swift:** Choose a container whose name and metadata you can share
  with Copilot's model provider. Prefer a [read-only Swift identity](../README.md#read-only-swift-deployment).
  For the wizard, you need an [OpenStack RC file](../README.md#openstack-rc-file)
  with that identity's credentials. Keep the file outside the checkout, owned
  by your user, with no permissions for group or other users.

## One-command quickstart

The wizard uses `quickstart/` as its Workshop project. It launches the Workshop
on the first run and starts it again on later runs. It keeps the container and
project files when you exit. The two-terminal alternative uses the same
Workshop. The wizard does not overwrite your project files or automatically
refresh the Workshop after definition changes.

Before starting the wizard, stop the Workshop if it is already running. If
the wizard reports an existing tunnel or token connection, inspect it before
retrying; the wizard will not disconnect connections it did not create. Do not
run the two-terminal setup at the same time as the wizard.

```sh
just quickstart
```

1. When prompted, enter the path to your RC file and the name of your test
   container. To skip these prompts, set the variables before running
   `just quickstart`:

   ```sh
   export SWIFT_MCP_OPENRC='/path/to/test-swift.rc'
   export SWIFT_MCP_CONTAINERS='["quickstart-test"]'
   ```

2. If the quickstart Copilot token is missing, the wizard opens the GitHub
   token form. Follow the on-screen instructions and paste the token only at
   the hidden prompt. The wizard stores it in your host keyring.
3. The wizard creates or reuses the Workshop's MCP caller key, starts Swift
   MCP on the host, and opens Copilot in the Workshop. You don't need to
   enter a key ID. Once Copilot opens, verify the connection below.

## Verify one Swift tool call

1. In Copilot, open `/mcp` and check that `swift-mcp` is listed.
2. Enter this prompt, replacing `quickstart-test` with your test container:

   > Use `get_container_stats` from `swift-mcp` for `quickstart-test`.
   > Show the object count and bytes used. Do not read any objects.

3. If Copilot asks for approval, approve only that call. Open the **tool
   result** and look for `object_count` and `bytes_used`. Those fields confirm
   that Swift returned container metadata. Copilot's summary alone doesn't
   confirm this, and `list_containers` only echoes the configured allowlist.

**If you used the wizard:** Exit Copilot. The wizard disconnects the token and
tunnel, stops Swift MCP and the Workshop, and removes the temporary server
environment. It prints the path to your saved project. The Copilot token and
[caller-key credentials](key-management.md) remain available for the next run.
If shutdown doesn't finish, see [Recover an interrupted wizard run](#recover-an-interrupted-wizard-run).

**If you used two terminals:** Follow [Stop and disconnect](#stop-and-disconnect).

## Manual two-terminal alternative

Use this path to run the server and Copilot from separate host terminals. It
uses the Workshop project in `quickstart/`, which is mounted at `/project`.
Make sure `secret-tool` is installed on the host (`libsecret-tools` on Ubuntu).

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

### 2. Install Swift MCP

In **terminal 1**, run:

```sh
uv sync --locked
```

### 3. Start the server and connect the Workshop

In **terminal 1**, choose how to supply Swift credentials:

- **RC file:** Swift MCP reads assignments without running shell commands.
  Set the path to your file:

  ```sh
  export SWIFT_MCP_OPENRC='/path/to/test-swift.rc'
  ```

- **Environment:** If your RC file needs to prompt for a password or run
  commands to set credentials, leave `SWIFT_MCP_OPENRC` unset. Follow the
  [host environment setup](../README.md#keystone-v3-password) in this terminal.

Set the allowlist to your test container and start the server:

```sh
export SWIFT_MCP_CONTAINERS='["quickstart-test"]'
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

### 4. Start Copilot in the Workshop

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

- **Manual Workshop launch or connection failed:** Run `workshop -p quickstart changes`,
  then `workshop -p quickstart tasks <change-id>` with the failed change's ID.
  After an interrupted setup, run `workshop -p quickstart connections` and
  disconnect any remaining quickstart connections before retrying.
- **Copilot asks you to sign in:** Check the `github-token` connection. For
  the two-terminal setup, run `workshop -p quickstart connections`. For the
  wizard, run `workshop -p "<printed project path>" connections`, replacing
  the placeholder with the path from `Workshop project:`. Check the token's
  keyring labels, expiration, and **Copilot Requests** permission.
- **Swift MCP returns HTTP 401:** Check the MCP caller key ID. In the
  two-terminal setup, `KEY_ID` must match the ID printed in terminal 1.

Do not print either secret while troubleshooting. See [more troubleshooting](../README.md#troubleshooting).

### Access files from a wizard run

Files in `/project` are also available at the printed host project path. To
access files elsewhere in the stopped Workshop, use that path to start it and
open a shell:

```sh
project='/path/printed/by/the/wizard'
workshop -p "$project" start
workshop -p "$project" shell
```

The Workshop consumes storage. `workshop remove` deletes its container files,
so back up anything you need before choosing to remove it. The project is in
your checkout and uses its existing permissions; files created in `/project`
may appear as untracked Git files. Do not write credentials into the Workshop
or its project, or commit sensitive files.

### Recover an interrupted wizard run

If the wizard couldn't stop its Workshop, use the project path it printed after
`Workshop project:`. From the repository root, this is `quickstart/`.

1. Check its connections and recent changes:

   ```sh
   project="$PWD/quickstart"
   workshop -p "$project" connections
   workshop -p "$project" changes
   ```

2. If a change failed, inspect it. Replace `42` with the ID from `changes`:

   ```sh
   workshop -p "$project" tasks 42
   ```

3. If the quickstart token or tunnel is still connected, disconnect only the
   connections still listed for this run:

   ```sh
   workshop -p "$project" disconnect swift-mcp-quickstart/copilot:github-token
   workshop -p "$project" disconnect swift-mcp-quickstart/copilot:swift-mcp
   ```

4. If the Workshop is still running, run `workshop -p "$project" stop`.
   This preserves files in both the project and the container.

Older wizard versions saved separate projects under
`~/.local/share/swift-mcp/quickstarts/`. This wizard leaves those projects
untouched. To access one, use its saved path with the commands above.

## Security notes

The tunnel does not authenticate callers. Any process in the Workshop can
request the connected Copilot token. Copilot and its child processes can
access the MCP caller key during the run. Keep approval prompts enabled and
keep Swift credentials on the host, outside the checkout and logs.

If this was a one-time test, revoke the [GitHub token](https://github.com/settings/personal-access-tokens)
and the [MCP caller key](key-management.md#routine-rotation) when finished.
