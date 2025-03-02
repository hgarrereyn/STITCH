import React, { useState } from 'react';
import { Box, Tabs, Tab } from '@mui/material';
import ConversationsPage from './components/ConversationsPage';
import Schema from './components/Schema';
import Sampler from './components/Sampler';
import Fuzz from './components/Fuzz';
import View from './components/View';
import Runtime from './components/Runtime';
import AutoFuzz from './components/AutoFuzz';

function App() {
  const [currentTab, setCurrentTab] = useState(0);

  const handleTabChange = (event, newValue) => {
    setCurrentTab(newValue);
  };

  return (
    <Box sx={{ flexGrow: 1, height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs value={currentTab} onChange={handleTabChange}>
          <Tab label="Conversations" />
          <Tab label="Schema" />
          <Tab label="Sampler" />
          <Tab label="Fuzz" />
          <Tab label="View" />
          <Tab label="Runtime" />
          <Tab label="AutoFuzz" />
        </Tabs>
      </Box>
      {currentTab === 0 && <ConversationsPage />}
      {currentTab === 1 && <Schema />}
      {currentTab === 2 && <Sampler />}
      {currentTab === 3 && <Fuzz />}
      {currentTab === 4 && <View />}
      {currentTab === 5 && <Runtime />}
      {currentTab === 6 && <AutoFuzz />}
    </Box>
  );
}

export default App; 