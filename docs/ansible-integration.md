# Ansible Integration Guide

This guide covers how to download artifacts from Magpie in Ansible playbooks,
with secure token handling and checksum verification.

## Overview

Magpie artifacts can be pulled from Ansible using the `ansible.builtin.get_url`
module with bearer token authentication. This is the recommended approach for:

- Deploying scripts and binaries to managed hosts
- Distributing configuration files
- Pulling game assets during competition setup
- Retrieving certificates or other security artifacts

## Prerequisites

- Magpie server accessible from Ansible control node or target hosts
- Read-scoped bearer token (created via `magpie-ctl token create --scope read`)
- Ansible Vault configured for secret storage

## Secure Token Storage

**Never store tokens in plaintext in playbooks or variable files.**

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

### Simple Artifact Download

```yaml
---
- name: Download artifact from Magpie
  hosts: all
  vars:
    magpie_server: "https://magpie.example.com"
    magpie_token: "{{ vault_magpie_token }}"

  tasks:
    - name: Download scoring engine
      ansible.builtin.get_url:
        url: "{{ magpie_server }}/artifacts/scoring/engine/latest"
        dest: /opt/scoring/engine
        headers:
          Authorization: "Bearer {{ magpie_token }}"
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
    headers:
      Authorization: "Bearer {{ magpie_token }}"
    mode: "0644"
```

### Download by Hash Reference

For immutable deployments, pin to a specific blob hash:

```yaml
- name: Download artifact by hash ref
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/tools/validator/blobs/a1b2c3d4"
    dest: /usr/local/bin/validator
    headers:
      Authorization: "Bearer {{ magpie_token }}"
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
      headers:
        Authorization: "Bearer {{ magpie_token }}"
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
      headers:
        Authorization: "Bearer {{ magpie_token }}"
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
    headers:
      Authorization: "Bearer {{ magpie_token }}"
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
    headers:
      Authorization: "Bearer {{ magpie_token }}"
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
        headers:
          Authorization: "Bearer {{ magpie_token }}"
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
    headers:
      Authorization: "Bearer {{ magpie_token }}"
    mode: "0755"
  when: not existing_binary.stat.exists
```

## Role Example

A reusable role for pulling Magpie artifacts:

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
```

### tasks/main.yml

```yaml
---
- name: Validate required variables
  ansible.builtin.assert:
    that:
      - magpie_artifact_path | length > 0
      - magpie_dest | length > 0
      - vault_magpie_token is defined
    fail_msg: "Required magpie_artifact variables not set"

- name: Download artifact from Magpie
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/{{ magpie_artifact_path }}/{{ magpie_artifact_tag }}"
    dest: "{{ magpie_dest }}"
    headers:
      Authorization: "Bearer {{ vault_magpie_token }}"
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

### 401 Unauthorized

The token is missing or invalid:

```yaml
# Verify token is being passed
- name: Debug token presence
  ansible.builtin.debug:
    msg: "Token starts with: {{ vault_magpie_token[:10] }}..."
  no_log: false  # Temporarily enable for debugging
```

Check that:
- The vault file is being decrypted (run with `--ask-vault-pass`)
- Variable name matches exactly (`vault_magpie_token`)
- Token has not been revoked on the server

### 403 Forbidden

Token lacks required permissions. For downloads, you need at least `read` scope.
Contact your Magpie administrator for a token with appropriate scope.

### 404 Not Found

The artifact path or tag does not exist:

```bash
# Verify artifact exists using curl
curl -H "Authorization: Bearer $TOKEN" \
  https://magpie.example.com/api/v1/artifacts/path/to/artifact
```

### Connection Timeouts

For large artifacts, increase the timeout:

```yaml
- name: Download large artifact
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/images/large-vm/latest"
    dest: /var/lib/images/vm.qcow2
    headers:
      Authorization: "Bearer {{ magpie_token }}"
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
      headers:
        Authorization: "Bearer {{ magpie_token }}"
      mode: "0755"
```

## Security Best Practices

1. **Use read-only tokens** - Create tokens with minimal scope for artifact pulls
2. **Vault-encrypt tokens** - Never store tokens in plaintext
3. **Use `no_log`** - Prevent token exposure in logs:
   ```yaml
   - name: Download sensitive artifact
     ansible.builtin.get_url:
       url: "{{ magpie_server }}/artifacts/secrets/cert/latest"
       dest: /etc/ssl/cert.pem
       headers:
         Authorization: "Bearer {{ magpie_token }}"
     no_log: true
   ```
4. **Pin versions for production** - Use specific tags or hash refs instead of `latest`
5. **Verify integrity** - For security-sensitive artifacts, use one of these approaches:
   - **Hash refs** (recommended): Download via `/blobs/<hash>` for content-addressed immutability
   - **Pre-defined checksums**: Store expected SHA-256 in vault-encrypted variables
6. **Rotate tokens periodically** - Update vault-stored tokens on a schedule

## See Also

- [User Guide](user-guide.md) - CLI usage and server administration
- [Authentik Integration](authentik-setup.md) - SSO for browser access
- [Design Document](design.md) - Architecture and design decisions

---

*This documentation was created with AI assistance (Claude Code w/ Opus 4.5).*
