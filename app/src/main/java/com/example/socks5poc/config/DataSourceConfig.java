package com.example.socks5poc.config;

import java.util.Properties;
import oracle.ucp.jdbc.PoolDataSource;
import oracle.ucp.jdbc.PoolDataSourceFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(AppProperties.class)
public class DataSourceConfig {

    @Bean
    public PoolDataSource dataSource(AppProperties props) throws Exception {
        PoolDataSource ds = PoolDataSourceFactory.getPoolDataSource();
        ds.setConnectionFactoryClassName("oracle.jdbc.pool.OracleDataSource");

        AppProperties.Db db = props.getDb();
        String url;
        if ("tls".equalsIgnoreCase(props.getAuthMode())) {
            url = db.getTlsUrl(); // full TLS connect string from ADB console
        } else {
            url = "jdbc:oracle:thin:@" + db.getTnsAlias()
                + "?TNS_ADMIN=" + db.getWalletPath();
        }
        ds.setURL(url);
        ds.setUser(db.getUser());
        ds.setPassword(db.getPassword());

        Properties p = new Properties();
        AppProperties.Socks socks = props.getSocks();
        p.setProperty("oracle.net.socksProxyHost", socks.getHost());
        p.setProperty("oracle.net.socksProxyPort", String.valueOf(socks.getPort()));
        p.setProperty("oracle.net.socksRemoteDNS", String.valueOf(socks.isRemoteDns()));
        ds.setConnectionProperties(p);

        ds.setValidateConnectionOnBorrow(true);
        ds.setSQLForValidateConnection("SELECT 1 FROM DUAL");
        ds.setInitialPoolSize(1);
        ds.setMinPoolSize(1);
        ds.setMaxPoolSize(5);
        return ds;
    }
}
