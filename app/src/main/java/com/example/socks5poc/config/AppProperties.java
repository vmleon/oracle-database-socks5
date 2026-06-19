package com.example.socks5poc.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("app")
public class AppProperties {
    private String authMode = "mtls";
    private Db db = new Db();
    private Socks socks = new Socks();

    public String getAuthMode() { return authMode; }
    public void setAuthMode(String authMode) { this.authMode = authMode; }
    public Db getDb() { return db; }
    public void setDb(Db db) { this.db = db; }
    public Socks getSocks() { return socks; }
    public void setSocks(Socks socks) { this.socks = socks; }

    public static class Db {
        private String tnsAlias;
        private String walletPath = "";
        private String tlsUrl = "";
        private String user;
        private String password;
        public String getTnsAlias() { return tnsAlias; }
        public void setTnsAlias(String v) { this.tnsAlias = v; }
        public String getWalletPath() { return walletPath; }
        public void setWalletPath(String v) { this.walletPath = v; }
        public String getTlsUrl() { return tlsUrl; }
        public void setTlsUrl(String v) { this.tlsUrl = v; }
        public String getUser() { return user; }
        public void setUser(String v) { this.user = v; }
        public String getPassword() { return password; }
        public void setPassword(String v) { this.password = v; }
    }

    public static class Socks {
        private String host;
        private int port = 1080;
        private String mode = "jumphost";
        private boolean remoteDns = true;
        public String getHost() { return host; }
        public void setHost(String v) { this.host = v; }
        public int getPort() { return port; }
        public void setPort(int v) { this.port = v; }
        public String getMode() { return mode; }
        public void setMode(String v) { this.mode = v; }
        public boolean isRemoteDns() { return remoteDns; }
        public void setRemoteDns(boolean v) { this.remoteDns = v; }
    }
}
