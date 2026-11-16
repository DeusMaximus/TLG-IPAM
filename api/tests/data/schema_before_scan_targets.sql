-- Schema of a database created by main at 3ee631b, before the scan_targets table and reserved_ranges.scan_excluded (#9).
-- Captured with: sqlite3 ipam.db .schema
CREATE TABLE device_types (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(40) NOT NULL, 
	icon VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE sections (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	colour VARCHAR(9), 
	PRIMARY KEY (id)
);
CREATE TABLE changes (
	id VARCHAR(36) NOT NULL, 
	ts DATETIME NOT NULL, 
	entity_type VARCHAR(16) NOT NULL, 
	entity_id VARCHAR(36) NOT NULL, 
	action VARCHAR(16) NOT NULL, 
	summary TEXT NOT NULL, 
	"before" JSON, 
	"after" JSON, 
	source VARCHAR(8) NOT NULL, 
	actor VARCHAR(80), 
	PRIMARY KEY (id)
);
CREATE TABLE agents (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	name_key VARCHAR(255) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	version VARCHAR(40), 
	platform VARCHAR(120), 
	hostname VARCHAR(253), 
	interfaces JSON, 
	last_checkin_at DATETIME, 
	last_error TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name_key)
);
CREATE TABLE subnets (
	id VARCHAR(36) NOT NULL, 
	section_id VARCHAR(36) NOT NULL, 
	cidr VARCHAR(43) NOT NULL, 
	vlan_id INTEGER, 
	name VARCHAR(120) NOT NULL, 
	domain VARCHAR(253), 
	gateway VARCHAR(39), 
	dns_servers JSON, 
	dhcp_start VARCHAR(39), 
	dhcp_end VARCHAR(39), 
	notes TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(section_id) REFERENCES sections (id)
);
CREATE TABLE tokens (
	id VARCHAR(36) NOT NULL, 
	kind VARCHAR(8) NOT NULL, 
	agent_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	token_prefix VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	last_used_at DATETIME, 
	expires_at DATETIME, 
	used_at DATETIME, 
	revoked_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id), 
	UNIQUE (token_hash)
);
CREATE INDEX ix_tokens_agent_id ON tokens (agent_id);
CREATE UNIQUE INDEX uq_tokens_live_agent_token ON tokens (agent_id) WHERE kind = 'agent' AND revoked_at IS NULL;
CREATE TABLE reserved_ranges (
	id VARCHAR(36) NOT NULL, 
	subnet_id VARCHAR(36) NOT NULL, 
	start_ip VARCHAR(39) NOT NULL, 
	end_ip VARCHAR(39) NOT NULL, 
	start_int BIGINT NOT NULL, 
	end_int BIGINT NOT NULL, 
	description VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(subnet_id) REFERENCES subnets (id)
);
CREATE TABLE hosts (
	id VARCHAR(36) NOT NULL, 
	subnet_id VARCHAR(36) NOT NULL, 
	ip VARCHAR(39) NOT NULL, 
	ip_int BIGINT NOT NULL, 
	hostname VARCHAR(253), 
	domain VARCHAR(253), 
	mac VARCHAR(17), 
	device_type VARCHAR(40) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	owner VARCHAR(120), 
	notes TEXT, 
	last_seen DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_hosts_subnet_ip UNIQUE (subnet_id, ip), 
	FOREIGN KEY(subnet_id) REFERENCES subnets (id)
);
