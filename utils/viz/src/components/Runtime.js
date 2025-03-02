import React, { useState, useEffect } from 'react';
import { Box, TextField, Grid, Typography } from '@mui/material';
import axios from 'axios';

const DEFAULT_PORT = process.env.REACT_APP_DEFAULT_LOG_PORT || '3000';
const DEFAULT_SCHEMA_URL = `http://localhost:${DEFAULT_PORT}/schema.json`;
const DEFAULT_RUNTIME_URL = `http://localhost:${DEFAULT_PORT}/runtime.json`;

function Runtime() {
  const [schemaUrl, setSchemaUrl] = useState(() => {
    const saved = localStorage.getItem('lastRuntimeSchemaUrl');
    return saved || DEFAULT_SCHEMA_URL;
  });
  const [runtimeUrl, setRuntimeUrl] = useState(() => {
    const saved = localStorage.getItem('lastRuntimeMetadataUrl');
    return saved || DEFAULT_RUNTIME_URL;
  });
  const [schema, setSchema] = useState({ types: [], endpoints: [] });
  const [runtimeMetadata, setRuntimeMetadata] = useState(null);
  const [error, setError] = useState(null);
  const [debouncedSchemaUrl, setDebouncedSchemaUrl] = useState(schemaUrl);
  const [debouncedRuntimeUrl, setDebouncedRuntimeUrl] = useState(runtimeUrl);

  // Update localStorage when URLs change
  useEffect(() => {
    localStorage.setItem('lastRuntimeSchemaUrl', schemaUrl);
  }, [schemaUrl]);

  useEffect(() => {
    localStorage.setItem('lastRuntimeMetadataUrl', runtimeUrl);
  }, [runtimeUrl]);

  // Debounce the URL changes
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSchemaUrl(schemaUrl);
    }, 500);
    return () => clearTimeout(timer);
  }, [schemaUrl]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedRuntimeUrl(runtimeUrl);
    }, 500);
    return () => clearTimeout(timer);
  }, [runtimeUrl]);

  const fetchSchema = async (url) => {
    try {
      if (!url || !url.startsWith('http')) {
        setError('Please enter a valid schema URL');
        return;
      }

      const cacheBustUrl = `${url}${url.includes('?') ? '&' : '?'}_t=${Date.now()}`;
      const response = await axios.get(cacheBustUrl);
      
      if (!response.data || !Array.isArray(response.data.types) || !Array.isArray(response.data.endpoints)) {
        setError('Invalid schema format');
        return;
      }

      setSchema(response.data);
      setError(null);
    } catch (error) {
      console.error('Error fetching schema:', error);
      setError('Error loading schema');
    }
  };

  const fetchRuntimeMetadata = async (url) => {
    try {
      if (!url || !url.startsWith('http')) {
        setError('Please enter a valid runtime metadata URL');
        return;
      }

      const cacheBustUrl = `${url}${url.includes('?') ? '&' : '?'}_t=${Date.now()}`;
      const response = await axios.get(cacheBustUrl);
      
      // For now, just validate that we got some data
      if (!response.data) {
        setError('Invalid runtime metadata format');
        return;
      }

      setRuntimeMetadata(response.data);
      setError(null);
    } catch (error) {
      console.error('Error fetching runtime metadata:', error);
      setError('Error loading runtime metadata');
    }
  };

  // Fetch schema when URL changes
  useEffect(() => {
    fetchSchema(debouncedSchemaUrl);
  }, [debouncedSchemaUrl]);

  // Fetch runtime metadata when URL changes and every 10 seconds
  useEffect(() => {
    fetchRuntimeMetadata(debouncedRuntimeUrl);

    const interval = setInterval(() => {
      fetchRuntimeMetadata(debouncedRuntimeUrl);
    }, 10000);

    return () => clearInterval(interval);
  }, [debouncedRuntimeUrl]);

  return (
    <Box sx={{ flexGrow: 1, height: 'calc(100vh - 48px)', overflow: 'hidden' }}>
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Grid container spacing={2}>
          <Grid item xs={6}>
            <TextField
              fullWidth
              label="Schema File URL"
              value={schemaUrl}
              onChange={(e) => setSchemaUrl(e.target.value)}
              variant="outlined"
              size="small"
              error={!!error}
              helperText={error}
            />
          </Grid>
          <Grid item xs={6}>
            <TextField
              fullWidth
              label="Runtime Metadata URL"
              value={runtimeUrl}
              onChange={(e) => setRuntimeUrl(e.target.value)}
              variant="outlined"
              size="small"
              error={!!error}
              helperText={`Updates every 10 seconds. Default port is ${DEFAULT_PORT}`}
            />
          </Grid>
        </Grid>
      </Box>
      <Box sx={{ p: 2 }}>
        {/* Content will be added here */}
      </Box>
    </Box>
  );
}

export default Runtime; 