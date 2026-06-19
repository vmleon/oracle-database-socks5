package com.example.socks5poc.health;

import com.example.socks5poc.config.AppProperties;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import oracle.ucp.jdbc.PoolDataSource;
import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.HealthIndicator;
import org.springframework.stereotype.Component;

@Component("db")
public class DatabaseHealthIndicator implements HealthIndicator {

    private final PoolDataSource ds;
    private final AppProperties props;

    public DatabaseHealthIndicator(PoolDataSource ds, AppProperties props) {
        this.ds = ds;
        this.props = props;
    }

    @Override
    public Health health() {
        long start = System.nanoTime();
        try (Connection c = ds.getConnection();
             Statement st = c.createStatement();
             ResultSet rs = st.executeQuery("SELECT 1 FROM DUAL")) {
            rs.next();
            long ms = (System.nanoTime() - start) / 1_000_000;
            return Health.up()
                .withDetail("latencyMs", ms)
                .withDetail("borrowed", ds.getBorrowedConnectionsCount())
                .withDetail("available", ds.getAvailableConnectionsCount())
                .withDetail("socks", props.getSocks().getHost() + ":" + props.getSocks().getPort())
                .withDetail("mode", props.getSocks().getMode())
                .build();
        } catch (Exception e) {
            return Health.down()
                .withDetail("error", sanitize(e))
                .withDetail("socks", props.getSocks().getHost() + ":" + props.getSocks().getPort())
                .withDetail("mode", props.getSocks().getMode())
                .build();
        }
    }

    private String sanitize(Exception e) {
        String name = e.getClass().getSimpleName();
        String msg = e.getMessage() == null ? "" : e.getMessage();
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("(ORA-\\d+|IO Error|UnknownHost)").matcher(msg);
        return m.find() ? name + ": " + m.group(1) : name;
    }
}
