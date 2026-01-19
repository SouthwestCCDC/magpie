# Ansible Integration Guide

This guide covers how to download artifacts from Magpie in Ansible playbooks,
with secure token handling and checksum verification.

## Overview

Magpie artifact downloads are **public by default** -- no authentication is required
to download from `/artifacts/*` endpoints. Simply use `ansible.builtin.get_url`
without any Authorization header for:

- Deploying scripts and binaries to managed hosts
- Distributing configuration files
- Pulling game assets during competition setup
- Retrieving certificates or other security artifacts

Bearer tokens are only required for **write operations** (uploads, tag management)
and **admin operations** (token management, garbage collection).

## Prerequisites

- Magpie server accessible from Ansible control node or target hosts
- (Optional) Bearer token -- only needed if SSO is enabled or for write operations
- Ansible Vault configured for secret storage (if using tokens)

## Secure Token Storage

> **Note:** Tokens are only required for write operations (uploads, tag management)
> and admin operations. For download-only playbooks, you can skip this section entirely.

**If you need tokens, never store them in plaintext in playbooks or variable files.**

### Creating an Encrypted Variables File

Create a vault-encrypted file for Magpie credentials:

```bash
# Create encrypted vars file
ansible-vault create group_vars/all/vault.yml
```

Add the token:

```yaml
# group_vars/all/vault.yml (encrypted)
vault_magpie_token: "mgp_your_read_token_here"
```

### Alternative: Single Variable Encryption

For simpler setups, encrypt just the token value:

```bash
ansible-vault encrypt_string 'mgp_your_read_token_here' --name 'vault_magpie_token'
```

Paste the output into your vars file.

## Basic Download Pattern

Artifact downloads from Magpie are public -- no authentication header is needed.

### Simple Artifact Download

```yaml
---
- name: Download artifact from Magpie
  hosts: all
  vars:
    magpie_server: "https://magpie.example.com"

  tasks:
    - name: Download scoring engine
      ansible.builtin.get_url:
        url: "{{ magpie_server }}/artifacts/scoring/engine/latest"
        dest: /opt/scoring/engine
        mode: "0755"
        owner: root
        group: root
```

### Download with Specific Tag

```yaml
- name: Download specific version of config bundle
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/configs/webapp/v2.1"
    dest: /etc/webapp/config.tar.gz
    mode: "0644"
```

### Download by Hash Reference

For immutable deployments, pin to a specific blob hash:

```yaml
- name: Download artifact by hash ref
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/tools/validator/blobs/a1b2c3d4"
    dest: /usr/local/bin/validator
    mode: "0755"
```

## Checksum Verification

### Using Hash References for Immutable Downloads

The most reliable verification method is to use Magpie's hash-based blob
references. When you download via a hash ref, you are guaranteed to get
exactly that content:

```yaml
vars:
  # Pin to specific blob hashes for verified, immutable deployments
  artifact_hashes:
    scoring_engine: "a1b2c3d4e5f6"  # Short hash from tag listing
    config_bundle: "9f8e7d6c5b4a"

tasks:
  - name: Download artifact by hash (content-verified)
    ansible.builtin.get_url:
      url: "{{ magpie_server }}/artifacts/scoring/engine/blobs/{{ artifact_hashes.scoring_engine }}"
      dest: /opt/scoring/engine
      mode: "0755"
```

Use `magpie tags <artifact-path>` to list tags with their blob hashes.

### Pre-defined Checksum in Variables

For critical artifacts, define expected checksums in your playbook or vars:

```yaml
vars:
  artifact_checksums:
    scoring_engine: "sha256:abc123def456..."
    config_bundle: "sha256:789xyz..."

tasks:
  - name: Download with known checksum
    ansible.builtin.get_url:
      url: "{{ magpie_server }}/artifacts/scoring/engine/stable"
      dest: /opt/scoring/engine
      checksum: "{{ artifact_checksums.scoring_engine }}"
      mode: "0755"
```

## Common Use Patterns

### Deploying Scripts

```yaml
- name: Deploy initialization scripts
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/scripts/{{ item.name }}/latest"
    dest: "/usr/local/bin/{{ item.name }}"
    mode: "0755"
  loop:
    - name: setup-network
    - name: configure-firewall
    - name: init-services
```

### Deploying Configuration Archives

```yaml
- name: Download configuration archive
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/configs/nginx/{{ config_version }}"
    dest: /tmp/nginx-config.tar.gz
    mode: "0644"

- name: Extract configuration
  ansible.builtin.unarchive:
    src: /tmp/nginx-config.tar.gz
    dest: /etc/nginx/
    remote_src: true
  notify: Reload nginx

- name: Clean up archive
  ansible.builtin.file:
    path: /tmp/nginx-config.tar.gz
    state: absent
```

### Installing Binaries with Version Pinning

```yaml
- name: Install application binary
  block:
    - name: Download binary
      ansible.builtin.get_url:
        url: "{{ magpie_server }}/artifacts/apps/myapp/{{ myapp_version | default('stable') }}"
        dest: /opt/myapp/bin/myapp
        mode: "0755"
        force: true  # Re-download if changed
      register: download_result

    - name: Restart service if binary changed
      ansible.builtin.systemd:
        name: myapp
        state: restarted
      when: download_result.changed
```

### Conditional Downloads

```yaml
- name: Check if artifact needs update
  ansible.builtin.stat:
    path: /opt/tools/analyzer
  register: existing_binary

- name: Download artifact only if missing
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/tools/analyzer/latest"
    dest: /opt/tools/analyzer
    mode: "0755"
  when: not existing_binary.stat.exists
```

## Role Example

A reusable role for pulling Magpie artifacts. Since downloads are public by default,
the token is optional and only needed if SSO is enabled on the server.

### Role Structure

```
roles/magpie_artifact/
  defaults/main.yml
  tasks/main.yml
```

### defaults/main.yml

```yaml
---
magpie_server: "https://magpie.example.com"
magpie_artifact_path: ""
magpie_artifact_tag: "latest"
magpie_dest: ""
magpie_mode: "0644"
magpie_owner: root
magpie_group: root
magpie_force: false
# Optional: only needed if SSO is enabled on the Magpie server
# magpie_token: "{{ vault_magpie_token }}"
```

### tasks/main.yml

```yaml
---
- name: Validate required variables
  ansible.builtin.assert:
    that:
      - magpie_artifact_path | length > 0
      - magpie_dest | length > 0
    fail_msg: "Required magpie_artifact variables not set"

- name: Download artifact from Magpie
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/{{ magpie_artifact_path }}/{{ magpie_artifact_tag }}"
    dest: "{{ magpie_dest }}"
    headers: "{{ {'Authorization': 'Bearer ' + magpie_token} if magpie_token is defined else omit }}"
    mode: "{{ magpie_mode }}"
    owner: "{{ magpie_owner }}"
    group: "{{ magpie_group }}"
    force: "{{ magpie_force }}"
  register: magpie_download_result
```

### Using the Role

```yaml
- name: Deploy scoring components
  hosts: scoring_servers
  roles:
    - role: magpie_artifact
      vars:
        magpie_artifact_path: "scoring/engine"
        magpie_artifact_tag: "stable"
        magpie_dest: /opt/scoring/engine
        magpie_mode: "0755"

    - role: magpie_artifact
      vars:
        magpie_artifact_path: "scoring/config"
        magpie_artifact_tag: "quals-2026"
        magpie_dest: /opt/scoring/config.yml
        magpie_mode: "0640"
        magpie_group: scoring
```

## Troubleshooting

### 401 Unauthorized / 403 Forbidden

**For artifact downloads:** These errors should NOT occur for public download endpoints
(`/artifacts/*`) unless SSO is enabled on the Magpie server. If you see these errors
when downloading artifacts:

1. Check if SSO is enabled -- if so, you need a token
2. Verify you are using the correct URL path (`/artifacts/...` not `/api/...`)

**For write operations (uploads, tag management):** Token is required. Check that:
- The vault file is being decrypted (run with `--ask-vault-pass`)
- Variable name matches exactly (`vault_magpie_token`)
- Token has not been revoked on the server
- Token has appropriate scope (`write` or `admin`)

### 404 Not Found

The artifact path or tag does not exist:

```bash
# Verify artifact exists using curl (no auth needed for downloads)
curl https://magpie.example.com/artifacts/path/to/artifact/latest
```

### Connection Timeouts

For large artifacts, increase the timeout:

```yaml
- name: Download large artifact
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/images/large-vm/latest"
    dest: /var/lib/images/vm.qcow2
    timeout: 600  # 10 minutes
    mode: "0644"
```

### Checksum Mismatch

If checksum verification fails:

1. Verify the checksum in your variables matches the current artifact version
2. Try re-downloading (possible network corruption)
3. Use hash refs instead of mutable tags for guaranteed consistency

For critical deployments, use hash-based blob references to guarantee content
integrity. Hash refs are immutable--they always return the same content:

```yaml
vars:
  # Get hash from: magpie tags app/binary
  app_binary_hash: "a1b2c3d4"

tasks:
  - name: Download by hash ref (guaranteed immutable)
    ansible.builtin.get_url:
      url: "{{ magpie_server }}/artifacts/app/binary/blobs/{{ app_binary_hash }}"
      dest: /opt/app/binary
      mode: "0755"
```

## Security Best Practices

1. **Downloads are public** - Magpie artifact downloads require no authentication by default.
   Tokens are only needed for write operations (uploads, tag management) or if SSO is enabled.
2. **Vault-encrypt tokens when needed** - If you use tokens for write operations, never store
   them in plaintext
3. **Use `no_log` for upload tasks** - Prevent token exposure in logs when uploading:
   ```yaml
   - name: Upload artifact to Magpie
     ansible.builtin.uri:
       url: "{{ magpie_server }}/api/v1/artifacts/path/tag"
       method: PUT
       headers:
         Authorization: "Bearer {{ magpie_token }}"
       src: /path/to/artifact
     no_log: true
   ```
4. **Pin versions for production** - Use specific tags or hash refs instead of `latest`
5. **Verify integrity** - For security-sensitive artifacts, use one of these approaches:
   - **Hash refs** (recommended): Download via `/blobs/<hash>` for content-addressed immutability
   - **Pre-defined checksums**: Store expected SHA-256 in variables
6. **Rotate tokens periodically** - If using tokens for write operations, update them on a schedule

## See Also

- [User Guide](user-guide.md) - CLI usage and server administration
- [Authentik Integration](authentik-setup.md) - SSO for browser access
- [Design Document](design.md) - Architecture and design decisions

---

*This documentation was created with AI assistance (Claude Code w/ Opus 4.5).*
