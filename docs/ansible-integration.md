# Ansible Integration Guide

This guide covers how to download artifacts from Magpie in Ansible playbooks,
with optional secure token handling and checksum verification.

## Overview

Magpie artifacts can be pulled from Ansible using the `ansible.builtin.get_url`
module. Bearer token authentication is optional for downloads (public by default)
but required for uploads and tag management. This is the recommended approach for:

- Deploying scripts and binaries to managed hosts
- Distributing configuration files
- Pulling game assets during competition setup
- Retrieving certificates or other security artifacts

## Prerequisites

- Magpie server accessible from Ansible control node or target hosts
- (Optional) Magpie bearer token if your playbooks will upload artifacts or manage tags
- (Recommended) Ansible Vault configured for secret storage when using bearer tokens

## Basic Download Pattern

Artifact downloads are public by default and do not require authentication.

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

## Secure Token Storage (For Write Operations)

Tokens are only required for write operations (uploads, tag management). If you
only need to download artifacts, you can skip this section.

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

Use `magpie ls <artifact-path>` to list tags with their blob hashes.

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
    fail_msg: "Required magpie_artifact variables not set"

- name: Download artifact from Magpie
  ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/{{ magpie_artifact_path }}/{{ magpie_artifact_tag }}"
    dest: "{{ magpie_dest }}"
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

For downloads: Check if Authentik SSO is enabled.

For write operations: Verify your token is decrypted, not revoked, and has appropriate scope.

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

If checksum verification fails, verify the checksum matches the current version, retry the download, or use hash refs for guaranteed consistency.

## Security Best Practices

1. **Token usage** - Downloads require authentication (unless under `/public/`). For write operations (uploads, tag mutations), use write scope tokens rather than admin scope unless admin operations are required.
2. **Vault-encrypt tokens** - Never store tokens in plaintext in Ansible variables.
3. **Use `no_log`** - Prevent token exposure in Ansible task output.
4. **Pin versions for production** - Use specific tags (not `latest`) for production deployments.
5. **Verify integrity** - Use hash refs or pre-defined checksums for security-sensitive artifacts.
6. **Establish token rotation procedures** - Having a tested rotation process ensures you can quickly respond to potential token disclosure events.

## See Also

- [User Guide](user-guide.md) - CLI usage and server administration
- [Authentik Integration](authentik-setup.md) - SSO for browser access
- [Design Document](design.md) - Architecture and design decisions

---

*This documentation was created with AI assistance (Claude Code w/ Opus 4.5).*
