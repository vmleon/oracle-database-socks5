package com.example.socks5poc.health;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

import com.example.socks5poc.config.AppProperties;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import oracle.ucp.jdbc.PoolDataSource;
import org.junit.jupiter.api.Test;
import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.Status;

class DatabaseHealthIndicatorTest {

    private AppProperties props() {
        AppProperties p = new AppProperties();
        p.getSocks().setHost("1.2.3.4");
        p.getSocks().setPort(1080);
        p.getSocks().setMode("jumphost");
        return p;
    }

    @Test
    void reportsUpWhenQuerySucceeds() throws Exception {
        PoolDataSource ds = mock(PoolDataSource.class);
        Connection conn = mock(Connection.class);
        Statement st = mock(Statement.class);
        ResultSet rs = mock(ResultSet.class);
        when(ds.getConnection()).thenReturn(conn);
        when(conn.createStatement()).thenReturn(st);
        when(st.executeQuery("SELECT 1 FROM DUAL")).thenReturn(rs);
        when(rs.next()).thenReturn(true);
        when(ds.getBorrowedConnectionsCount()).thenReturn(1);
        when(ds.getAvailableConnectionsCount()).thenReturn(0);

        Health h = new DatabaseHealthIndicator(ds, props()).health();

        assertThat(h.getStatus()).isEqualTo(Status.UP);
        assertThat(h.getDetails()).containsKeys("latencyMs", "socks", "mode");
        assertThat(h.getDetails().get("socks")).isEqualTo("1.2.3.4:1080");
    }

    @Test
    void reportsDownWithSanitizedErrorOnFailure() throws Exception {
        PoolDataSource ds = mock(PoolDataSource.class);
        when(ds.getConnection()).thenThrow(new java.sql.SQLException("ORA-12545 secret host details"));

        Health h = new DatabaseHealthIndicator(ds, props()).health();

        assertThat(h.getStatus()).isEqualTo(Status.DOWN);
        assertThat(String.valueOf(h.getDetails().get("error"))).doesNotContain("secret host details");
    }
}
