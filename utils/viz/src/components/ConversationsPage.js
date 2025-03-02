import React, { useState, useEffect } from 'react';
import { Box, TextField, Grid, List, ListItem, ListItemButton, ListItemText, Typography, Paper, Badge } from '@mui/material';
import Conversation from './Conversation';
import axios from 'axios';

const DEFAULT_PORT = process.env.REACT_APP_DEFAULT_LOG_PORT || '3000';
const DEFAULT_LOG_URL = `http://localhost:${DEFAULT_PORT}/log.json`;

function ConversationsPage() {
  const [logUrl, setLogUrl] = useState(() => {
    const saved = localStorage.getItem('lastLogUrl');
    return saved || DEFAULT_LOG_URL;
  });
  const [logs, setLogs] = useState([]);
  const [selectedLog, setSelectedLog] = useState(null);

  // Group logs by name and type, keeping only the most recent retry
  const processLogs = (rawLogs) => {
    const groupedLogs = [];
    const logMap = new Map();

    // Process logs in reverse to get most recent first
    for (let i = rawLogs.length - 1; i >= 0; i--) {
      const log = rawLogs[i];
      const key = `${log.meta.name}-${log.meta.type}`;
      
      if (!logMap.has(key)) {
        // Count retries by looking backwards until we hit a non-retry message
        let retryCount = 0;
        let j = i;
        while (j >= 0) {
          const currentLog = rawLogs[j];
          if (currentLog.meta.name === log.meta.name && 
              currentLog.meta.type === log.meta.type) {
            if (!currentLog.meta.is_retry) {
              // Found the original message, stop counting
              break;
            }
            retryCount++;
          }
          j--;
        }
        
        logMap.set(key, {
          ...log,
          retryCount: retryCount > 0 ? retryCount : 0,
          originalIndex: i
        });
      }
    }

    // Convert map back to array and sort by original index
    return Array.from(logMap.values())
      .sort((a, b) => a.originalIndex - b.originalIndex);
  };

  const fetchLogs = async () => {
    try {
      const response = await axios.get(logUrl);
      setLogs(processLogs(response.data));
    } catch (error) {
      console.error('Error fetching logs:', error);
    }
  };

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(fetchLogs, 5000); // Refresh every 5 seconds
    return () => clearInterval(interval);
  }, [logUrl]);

  // Update localStorage when URL changes
  useEffect(() => {
    localStorage.setItem('lastLogUrl', logUrl);
  }, [logUrl]);

  return (
    <Box sx={{ flexGrow: 1, height: 'calc(100vh - 48px)', overflow: 'hidden' }}>
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <TextField
          fullWidth
          label="Log File URL"
          value={logUrl}
          onChange={(e) => setLogUrl(e.target.value)}
          variant="outlined"
          size="small"
          helperText={`Default port is ${DEFAULT_PORT}. Change the port by setting REACT_APP_DEFAULT_LOG_PORT environment variable.`}
        />
      </Box>
      <Grid container sx={{ height: 'calc(100% - 80px)' }}>
        {/* Sidebar */}
        <Grid item xs={3} sx={{ borderRight: 1, borderColor: 'divider', height: '100%', overflow: 'auto' }}>
          <List>
            {logs.map((log, index) => (
              <ListItem key={index} disablePadding>
                <ListItemButton 
                  selected={selectedLog === index}
                  onClick={() => setSelectedLog(index)}
                >
                  <ListItemText
                    primary={
                      <Box sx={{ display: 'flex', alignItems: 'center' }}>
                        {log.meta.name}
                        {log.retryCount > 0 && (
                          <Badge 
                            badgeContent={log.retryCount}
                            color="error"
                            sx={{ ml: 2 }}
                          />
                        )}
                      </Box>
                    }
                    secondary={
                      <Typography component="span" variant="body2" color="text.primary">
                        {log.meta.type}
                      </Typography>
                    }
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </Grid>
        
        {/* Main content */}
        <Grid item xs={9} sx={{ height: '100%', overflow: 'auto', bgcolor: '#f5f5f5' }}>
          {selectedLog !== null && logs[selectedLog] && (
            <Box sx={{ p: 2 }}>
              <Paper sx={{ p: 2, mb: 2 }}>
                <Typography variant="h6">Metadata</Typography>
                <Typography>Type: {logs[selectedLog].meta.type}</Typography>
                <Typography>Name: {logs[selectedLog].meta.name}</Typography>
                <Typography>Retry Count: {logs[selectedLog].retryCount}</Typography>
              </Paper>
              <Conversation messages={logs[selectedLog].messages} />
            </Box>
          )}
        </Grid>
      </Grid>
    </Box>
  );
}

export default ConversationsPage; 