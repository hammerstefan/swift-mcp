# Swift MCP storage

This context exposes a bounded, read-only view of OpenStack Swift storage to authenticated MCP callers. A caller's MCP identity is distinct from the Swift identity used to contact storage.

## Language

**Swift account**:
The storage namespace associated with the configured Swift identity. It contains containers, not MCP users.
_Avoid_: User account, MCP account

**Swift identity**:
The credentials and project scope with which the server accesses Swift. All authorized MCP callers share this identity in the initial design.
_Avoid_: MCP user, client identity

**MCP caller**:
An OpenCode or Copilot CLI client allowed to request the server's view of Swift. Its authentication to the MCP server is separate from authentication to Swift.
_Avoid_: Swift user

**Container**:
A named grouping of objects within a Swift account. A container is not a filesystem directory.
_Avoid_: Folder, bucket

**Object**:
A named sequence of bytes and associated metadata within a container. Its name may contain slashes without implying filesystem directories.
_Avoid_: File, path

**Current object**:
The object version returned by an ordinary read without selecting a historical version. Historical versions are outside the initial read surface.

**Container allowlist**:
An optional operator-defined set of container names that narrows the server's view within the Swift identity's permissions. An unset allowlist means the Swift identity's full readable scope; an explicitly empty one means no containers.
