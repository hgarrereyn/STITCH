import React, { useState, useEffect, useRef } from 'react';
import { Box, TextField, Typography, Grid, Tabs, Tab, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, CircularProgress, Tooltip, IconButton, Button, Dialog, DialogTitle, DialogContent, DialogActions, Divider, Menu, MenuItem } from '@mui/material';
import axios from 'axios';
import NewReleasesIcon from '@mui/icons-material/NewReleases';
import AssignmentIcon from '@mui/icons-material/Assignment';
import BuildIcon from '@mui/icons-material/Build';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import RefreshIcon from '@mui/icons-material/Refresh';
import WarningIcon from '@mui/icons-material/Warning';
import CodeIcon from '@mui/icons-material/Code';
import FilterAltIcon from '@mui/icons-material/FilterAlt';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { diffLines } from 'diff';

const DEFAULT_PORT = process.env.REACT_APP_DEFAULT_LOG_PORT || '3000';
const REFRESH_INTERVAL = 10000; // 10 seconds

// Crash status types
const CRASH_STATUS = {
  NEW: 'new',
  NEW_RARE: 'new_rare',
  TRIAGED: 'triaged',
  PATCH_PENDING: 'patch_pending',
  FAILED_PATCH: 'failed_patch',
  OBSOLETE_PATCHED: 'obsolete_patched',
  OBSOLETE_REPLACED: 'obsolete_replaced',
  OBSOLETE: 'obsolete'
};

// Create a completely standalone refresh indicator component
const RefreshProgressIndicator = ({ interval, onRefresh }) => {
  const [progress, setProgress] = useState(0);
  const progressTimerRef = useRef(null);
  const refreshTimerRef = useRef(null);

  useEffect(() => {
    // Clear existing timers
    if (progressTimerRef.current) clearInterval(progressTimerRef.current);
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    
    // Reset progress
    setProgress(0);
    
    // Setup progress timer (updates every 100ms)
    progressTimerRef.current = setInterval(() => {
      setProgress(prev => {
        const newProgress = prev + (100 / (interval / 100));
        return newProgress > 100 ? 100 : newProgress;
      });
    }, 100);
    
    // Setup refresh timer
    refreshTimerRef.current = setTimeout(() => {
      if (onRefresh) {
        onRefresh();
      }
      // Reset progress after refresh
      setProgress(0);
    }, interval);
    
    return () => {
      if (progressTimerRef.current) clearInterval(progressTimerRef.current);
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [interval, onRefresh]);
  
  return (
    <Tooltip title={`Auto-refreshes every ${interval/1000} seconds`}>
      <Box sx={{ ml: 1, display: 'inline-flex' }}>
        <CircularProgress
          variant="determinate"
          value={progress}
          size={16}
          thickness={6}
          sx={{ color: 'primary.main' }}
        />
      </Box>
    </Tooltip>
  );
};

function AutoFuzz() {
  const [fuzzDirectory, setFuzzDirectory] = useState('');
  const [stageCount, setStageCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [currentTab, setCurrentTab] = useState(0);
  const [stageDirectories, setStageDirectories] = useState([]);
  const [crashData, setCrashData] = useState([]);
  const [loadingCrashes, setLoadingCrashes] = useState(false);
  const [crashError, setCrashError] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  
  // New state variable for fuzzer schema data
  const [fuzzerSchemaData, setFuzzerSchemaData] = useState({});
  
  // Dialog state for displaying patch details
  const [patchDialogOpen, setPatchDialogOpen] = useState(false);
  const [currentPatchData, setCurrentPatchData] = useState(null);
  const [patchDialogTitle, setPatchDialogTitle] = useState('');
  const [originalTemplate, setOriginalTemplate] = useState('');
  const [currentCrashStage, setCurrentCrashStage] = useState('');
  
  // State for dropdown menu
  const [menuAnchorEl, setMenuAnchorEl] = useState(null);
  const [activeMenuCrashHash, setActiveMenuCrashHash] = useState(null);
  const [hoverTimerRef, setHoverTimerRef] = useState(null);
  
  // New state for hiding obsolete replaced crashes
  const [hideObsoleteReplaced, setHideObsoleteReplaced] = useState(false);
  
  // Refs to store the latest data for background updates
  const latestStageDirectoriesRef = useRef([]);
  const latestCrashDataRef = useRef([]);
  const latestFuzzerSchemaDataRef = useRef({});

  // Load fuzz directory from localStorage on component mount
  useEffect(() => {
    const savedDirectory = localStorage.getItem('autoFuzzDirectory');
    if (savedDirectory) {
      setFuzzDirectory(savedDirectory);
    }
    
    // No need to clean up timers here anymore
  }, []);

  // Handle refresh triggered by the RefreshProgressIndicator
  const handleRefresh = () => {
    if (fuzzDirectory) {
      backgroundFetchStages();
    }
  };

  // Background fetch stages function that doesn't set loading states
  const backgroundFetchStages = async () => {
    try {
      const url = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}`;
      const response = await axios.get(url);
      
      // Parse HTML directory listing
      const htmlContent = response.data;
      
      // Use regex to find all links matching stage_X/ pattern
      const stageRegex = /<a href="(stage_\d+\/)"/g;
      const matches = [...htmlContent.matchAll(stageRegex)];
      const stages = matches.map(match => match[1].replace('/', ''));
      
      // Sort stages numerically
      stages.sort((a, b) => {
        const numA = parseInt(a.replace('stage_', ''));
        const numB = parseInt(b.replace('stage_', ''));
        return numA - numB;
      });
      
      // Update refs first
      latestStageDirectoriesRef.current = stages;
      
      // Then update state (this will trigger UI updates)
      setStageDirectories(stages);
      setStageCount(stages.length);
      
      // Fetch fuzzer schema for each stage
      await fetchFuzzerSchemas(stages);
      
      // If we're on the crashes tab, also fetch crash data in the background
      if (currentTab === 1) {
        backgroundFetchCrashData(stages);
      }
    } catch (err) {
      console.error('Error fetching stages:', err);
      // Only update error state if we don't already have data
      if (stageCount === 0) {
        setError('Failed to fetch stages');
      }
    }
    
    return Promise.resolve();
  };

  // Fetch fuzzer schema for each stage
  const fetchFuzzerSchemas = async (stages) => {
    try {
      const schemaMap = {};
      
      for (const stageDir of stages) {
        const fuzzerSchemaUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/fuzzer.json`;
        
        try {
          const schemaResponse = await axios.get(fuzzerSchemaUrl);
          schemaMap[stageDir] = schemaResponse.data;
        } catch (err) {
          console.log(`No fuzzer.json found in ${stageDir}, skipping`);
        }
      }
      
      // Update ref and state
      latestFuzzerSchemaDataRef.current = schemaMap;
      setFuzzerSchemaData(schemaMap);
    } catch (err) {
      console.error('Error fetching fuzzer schemas:', err);
    }
  };

  // Background fetch crash data without setting loading states
  const backgroundFetchCrashData = async (stages) => {
    try {
      // Create arrays to store crash data
      const crashArray = [];
      
      // Get the most recent stage
      const mostRecentStage = stages[stages.length - 1];
      const mostRecentStageNum = parseInt(mostRecentStage.replace('stage_', ''));
      
      // Create a map to track crashes across stages
      const crashesAcrossStages = {};
      
      // First pass: collect all crashes across all stages
      for (const stageDir of stages) {
        const stageNum = parseInt(stageDir.replace('stage_', ''));
        
        // Get crash buckets directory
        const crashBucketsUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/`;
        let crashBucketsResponse;
        
        try {
          crashBucketsResponse = await axios.get(crashBucketsUrl);
        } catch (err) {
          console.log(`No crash_buckets directory in ${stageDir}, skipping`);
          continue;
        }
        
        // Parse HTML to find crash hash directories
        const htmlContent = crashBucketsResponse.data;
        const crashDirRegex = /<a href="([0-9a-f]{64}\/)"/g;
        const crashMatches = [...htmlContent.matchAll(crashDirRegex)];
        
        // Process each crash hash directory
        for (const crashMatch of crashMatches) {
          const crashHash = crashMatch[1].replace('/', '');
          
          // Initialize tracking for this crash if not already done
          if (!crashesAcrossStages[crashHash]) {
            crashesAcrossStages[crashHash] = {
              stages: [],
              patchedInStage: null
            };
          }
          
          // Add this stage to the list of stages where this crash appears
          crashesAcrossStages[crashHash].stages.push({
            stageDir,
            stageNum
          });
          
          // Check if this crash has a patch in this stage
          const crashDirUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/`;
          const crashDirResponse = await axios.get(crashDirUrl);
          
          if (crashDirResponse.data.includes('href="patch.json"')) {
            // If we haven't recorded a patch for this crash yet, or this is an earlier stage
            // than the one we've recorded, update the patchedInStage
            if (crashesAcrossStages[crashHash].patchedInStage === null || 
                stageNum < crashesAcrossStages[crashHash].patchedInStage.stageNum) {
              crashesAcrossStages[crashHash].patchedInStage = {
                stageDir,
                stageNum
              };
            }
          }
        }
      }
      
      // Second pass: process each stage directory with the knowledge of crashes across stages
      for (const stageDir of stages) {
        // Get crash buckets directory
        const crashBucketsUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/`;
        let crashBucketsResponse;
        
        try {
          crashBucketsResponse = await axios.get(crashBucketsUrl);
        } catch (err) {
          console.log(`No crash_buckets directory in ${stageDir}, skipping`);
          continue;
        }
        
        // Parse HTML to find crash hash directories
        const htmlContent = crashBucketsResponse.data;
        const crashDirRegex = /<a href="([0-9a-f]{64}\/)"/g;
        const crashMatches = [...htmlContent.matchAll(crashDirRegex)];
        
        // Process each crash hash directory
        for (const crashMatch of crashMatches) {
          const crashHash = crashMatch[1].replace('/', '');
          
          // Get description.txt
          const descriptionUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/description.txt`;
          let description = '';
          
          try {
            const descResponse = await axios.get(descriptionUrl);
            description = descResponse.data.trim();
          } catch (err) {
            description = 'No description available';
          }
          
          // Count crash files (looking for .graph files)
          const crashDirUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/`;
          const crashDirResponse = await axios.get(crashDirUrl);
          const graphFileRegex = /<a href="([^"]+\.graph)"/g;
          const graphMatches = [...crashDirResponse.data.matchAll(graphFileRegex)];
          const count = graphMatches.length;
          
          // Check for triage.json and patch.json
          let hasTriage = false;
          let hasPatch = false;
          let triageData = null;
          let patchData = null;
          
          // Look for triage.json in the HTML content
          if (crashDirResponse.data.includes('href="triage.json"')) {
            hasTriage = true;
            
            // Fetch triage.json content for this specific crash in this specific stage
            try {
              const triageUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/triage.json`;
              const triageResponse = await axios.get(triageUrl);
              triageData = triageResponse.data;
            } catch (err) {
              console.error(`Error fetching triage data for ${crashHash} in ${stageDir}:`, err);
            }
          }
          
          // Look for patch.json in the HTML content
          if (crashDirResponse.data.includes('href="patch.json"')) {
            hasPatch = true;
            
            // Fetch patch.json content for this specific crash in this specific stage
            try {
              const patchUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/patch.json`;
              const patchResponse = await axios.get(patchUrl);
              patchData = patchResponse.data;
            } catch (err) {
              console.error(`Error fetching patch data for ${crashHash} in ${stageDir}:`, err);
            }
          }
          
          // Create a separate entry for this crash in this stage
          const stageNum = parseInt(stageDir.replace('stage_', ''));
          const isInMostRecentStage = stageNum === mostRecentStageNum;
          
          // Get information about this crash across stages
          const crashInfo = crashesAcrossStages[crashHash];
          const appearsInLaterStage = crashInfo.stages.some(s => s.stageNum > stageNum);
          const wasPatched = crashInfo.patchedInStage !== null && crashInfo.patchedInStage.stageNum <= stageNum;
          
          // Determine status for this crash in this stage
          let status;
          if (isInMostRecentStage) {
            if (wasPatched) {
              // If it was patched in an earlier stage but still appears in the most recent stage
              status = CRASH_STATUS.FAILED_PATCH;
            } else if (hasPatch) {
              status = CRASH_STATUS.PATCH_PENDING;
            } else if (hasTriage) {
              status = CRASH_STATUS.TRIAGED;
            } else if (count <= 4) {
              status = CRASH_STATUS.NEW_RARE;
            } else {
              status = CRASH_STATUS.NEW;
            }
          } else {
            if (wasPatched && appearsInLaterStage) {
              // If it was patched in this or earlier stage but still appears in a later stage
              // Mark it as a failed patch (this takes precedence over obsolete replaced)
              status = CRASH_STATUS.FAILED_PATCH;
            } else if (appearsInLaterStage) {
              // If this crash appears in a later stage, it's obsolete (replaced)
              status = CRASH_STATUS.OBSOLETE_REPLACED;
            } else if (hasPatch) {
              status = CRASH_STATUS.OBSOLETE_PATCHED;
            } else {
              status = CRASH_STATUS.OBSOLETE;
            }
          }
          
          // Add this crash instance to the array
          crashArray.push({
            hash: crashHash,
            description,
            stage: stageDir,
            count,
            hasTriage,
            hasPatch,
            status,
            triageData,
            patchData
          });
        }
      }
      
      // Sort by stage number (descending), then by hash, then by count
      crashArray.sort((a, b) => {
        // First sort by stage number (descending)
        const stageA = parseInt(a.stage.replace('stage_', ''));
        const stageB = parseInt(b.stage.replace('stage_', ''));
        
        if (stageA !== stageB) {
          return stageB - stageA; // Descending order for stages
        }
        
        // If stages are the same, sort by hash
        if (a.hash !== b.hash) {
          return a.hash.localeCompare(b.hash);
        }
        
        // If hash is the same, sort by count (descending)
        return b.count - a.count;
      });
      
      // Update refs and state
      latestCrashDataRef.current = crashArray;
      setCrashData(crashArray);
    } catch (err) {
      console.error('Error fetching crash data:', err);
      // Only update error state if we don't already have data
      if (crashData.length === 0) {
        setCrashError('Failed to fetch crash data');
      }
    }
  };

  // Fetch stage folders when fuzz directory changes
  useEffect(() => {
    if (!fuzzDirectory) {
      setStageCount(0);
      setStageDirectories([]);
      return;
    }

    // Initial load should show loading indicator
    setLoading(true);
    fetchStages();
  }, [fuzzDirectory]);

  // Fetch stages function (for initial load)
  const fetchStages = async () => {
    setLoading(true);
    setError(null);
    try {
      const url = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}`;
      const response = await axios.get(url);
      
      // Parse HTML directory listing
      const htmlContent = response.data;
      
      // Use regex to find all links matching stage_X/ pattern
      const stageRegex = /<a href="(stage_\d+\/)"/g;
      const matches = [...htmlContent.matchAll(stageRegex)];
      const stages = matches.map(match => match[1].replace('/', ''));
      
      // Sort stages numerically
      stages.sort((a, b) => {
        const numA = parseInt(a.replace('stage_', ''));
        const numB = parseInt(b.replace('stage_', ''));
        return numA - numB;
      });
      
      latestStageDirectoriesRef.current = stages;
      setStageDirectories(stages);
      setStageCount(stages.length);
      
      // Fetch fuzzer schema for each stage
      await fetchFuzzerSchemas(stages);
    } catch (err) {
      console.error('Error fetching stages:', err);
      setError('Failed to fetch stages');
      setStageCount(0);
      setStageDirectories([]);
    } finally {
      setLoading(false);
    }
    
    // Return a promise that resolves when done
    return Promise.resolve();
  };

  // Fetch crash data when stage directories change or when the Crashes tab is selected
  useEffect(() => {
    if (currentTab !== 1 || stageDirectories.length === 0 || !fuzzDirectory) {
      return;
    }

    // Only show loading indicator for initial load
    if (crashData.length === 0) {
      setLoadingCrashes(true);
    }
    fetchCrashData();
  }, [stageDirectories, currentTab, fuzzDirectory]);

  const fetchCrashData = async () => {
    if (crashData.length === 0) {
      setLoadingCrashes(true);
    }
    setCrashError(null);
    
    try {
      // Create arrays to store crash data
      const crashArray = [];
      
      // Get the most recent stage
      const mostRecentStage = stageDirectories[stageDirectories.length - 1];
      const mostRecentStageNum = parseInt(mostRecentStage.replace('stage_', ''));
      
      // Create a map to track crashes across stages
      const crashesAcrossStages = {};
      
      // First pass: collect all crashes across all stages
      for (const stageDir of stageDirectories) {
        const stageNum = parseInt(stageDir.replace('stage_', ''));
        
        // Get crash buckets directory
        const crashBucketsUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/`;
        let crashBucketsResponse;
        
        try {
          crashBucketsResponse = await axios.get(crashBucketsUrl);
        } catch (err) {
          console.log(`No crash_buckets directory in ${stageDir}, skipping`);
          continue;
        }
        
        // Parse HTML to find crash hash directories
        const htmlContent = crashBucketsResponse.data;
        const crashDirRegex = /<a href="([0-9a-f]{64}\/)"/g;
        const crashMatches = [...htmlContent.matchAll(crashDirRegex)];
        
        // Process each crash hash directory
        for (const crashMatch of crashMatches) {
          const crashHash = crashMatch[1].replace('/', '');
          
          // Initialize tracking for this crash if not already done
          if (!crashesAcrossStages[crashHash]) {
            crashesAcrossStages[crashHash] = {
              stages: [],
              patchedInStage: null
            };
          }
          
          // Add this stage to the list of stages where this crash appears
          crashesAcrossStages[crashHash].stages.push({
            stageDir,
            stageNum
          });
          
          // Check if this crash has a patch in this stage
          const crashDirUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/`;
          const crashDirResponse = await axios.get(crashDirUrl);
          
          if (crashDirResponse.data.includes('href="patch.json"')) {
            // If we haven't recorded a patch for this crash yet, or this is an earlier stage
            // than the one we've recorded, update the patchedInStage
            if (crashesAcrossStages[crashHash].patchedInStage === null || 
                stageNum < crashesAcrossStages[crashHash].patchedInStage.stageNum) {
              crashesAcrossStages[crashHash].patchedInStage = {
                stageDir,
                stageNum
              };
            }
          }
        }
      }
      
      // Second pass: process each stage directory with the knowledge of crashes across stages
      for (const stageDir of stageDirectories) {
        // Get crash buckets directory
        const crashBucketsUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/`;
        let crashBucketsResponse;
        
        try {
          crashBucketsResponse = await axios.get(crashBucketsUrl);
        } catch (err) {
          console.log(`No crash_buckets directory in ${stageDir}, skipping`);
          continue;
        }
        
        // Parse HTML to find crash hash directories
        const htmlContent = crashBucketsResponse.data;
        const crashDirRegex = /<a href="([0-9a-f]{64}\/)"/g;
        const crashMatches = [...htmlContent.matchAll(crashDirRegex)];
        
        // Process each crash hash directory
        for (const crashMatch of crashMatches) {
          const crashHash = crashMatch[1].replace('/', '');
          
          // Get description.txt
          const descriptionUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/description.txt`;
          let description = '';
          
          try {
            const descResponse = await axios.get(descriptionUrl);
            description = descResponse.data.trim();
          } catch (err) {
            description = 'No description available';
          }
          
          // Count crash files (looking for .graph files)
          const crashDirUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/`;
          const crashDirResponse = await axios.get(crashDirUrl);
          const graphFileRegex = /<a href="([^"]+\.graph)"/g;
          const graphMatches = [...crashDirResponse.data.matchAll(graphFileRegex)];
          const count = graphMatches.length;
          
          // Check for triage.json and patch.json
          let hasTriage = false;
          let hasPatch = false;
          let triageData = null;
          let patchData = null;
          
          // Look for triage.json in the HTML content
          if (crashDirResponse.data.includes('href="triage.json"')) {
            hasTriage = true;
            
            // Fetch triage.json content for this specific crash in this specific stage
            try {
              const triageUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/triage.json`;
              const triageResponse = await axios.get(triageUrl);
              triageData = triageResponse.data;
            } catch (err) {
              console.error(`Error fetching triage data for ${crashHash} in ${stageDir}:`, err);
            }
          }
          
          // Look for patch.json in the HTML content
          if (crashDirResponse.data.includes('href="patch.json"')) {
            hasPatch = true;
            
            // Fetch patch.json content for this specific crash in this specific stage
            try {
              const patchUrl = `http://localhost:${DEFAULT_PORT}/${fuzzDirectory}/${stageDir}/crash_buckets/${crashHash}/patch.json`;
              const patchResponse = await axios.get(patchUrl);
              patchData = patchResponse.data;
            } catch (err) {
              console.error(`Error fetching patch data for ${crashHash} in ${stageDir}:`, err);
            }
          }
          
          // Create a separate entry for this crash in this stage
          const stageNum = parseInt(stageDir.replace('stage_', ''));
          const isInMostRecentStage = stageNum === mostRecentStageNum;
          
          // Get information about this crash across stages
          const crashInfo = crashesAcrossStages[crashHash];
          const appearsInLaterStage = crashInfo.stages.some(s => s.stageNum > stageNum);
          const wasPatched = crashInfo.patchedInStage !== null && crashInfo.patchedInStage.stageNum <= stageNum;
          
          // Determine status for this crash in this stage
          let status;
          if (isInMostRecentStage) {
            if (wasPatched) {
              // If it was patched in an earlier stage but still appears in the most recent stage
              status = CRASH_STATUS.FAILED_PATCH;
            } else if (hasPatch) {
              status = CRASH_STATUS.PATCH_PENDING;
            } else if (hasTriage) {
              status = CRASH_STATUS.TRIAGED;
            } else if (count <= 4) {
              status = CRASH_STATUS.NEW_RARE;
            } else {
              status = CRASH_STATUS.NEW;
            }
          } else {
            if (wasPatched && appearsInLaterStage) {
              // If it was patched in this or earlier stage but still appears in a later stage
              // Mark it as a failed patch (this takes precedence over obsolete replaced)
              status = CRASH_STATUS.FAILED_PATCH;
            } else if (appearsInLaterStage) {
              // If this crash appears in a later stage, it's obsolete (replaced)
              status = CRASH_STATUS.OBSOLETE_REPLACED;
            } else if (hasPatch) {
              status = CRASH_STATUS.OBSOLETE_PATCHED;
            } else {
              status = CRASH_STATUS.OBSOLETE;
            }
          }
          
          // Add this crash instance to the array
          crashArray.push({
            hash: crashHash,
            description,
            stage: stageDir,
            count,
            hasTriage,
            hasPatch,
            status,
            triageData,
            patchData
          });
        }
      }
      
      // Sort by stage number (descending), then by hash, then by count
      crashArray.sort((a, b) => {
        // First sort by stage number (descending)
        const stageA = parseInt(a.stage.replace('stage_', ''));
        const stageB = parseInt(b.stage.replace('stage_', ''));
        
        if (stageA !== stageB) {
          return stageB - stageA; // Descending order for stages
        }
        
        // If stages are the same, sort by hash
        if (a.hash !== b.hash) {
          return a.hash.localeCompare(b.hash);
        }
        
        // If hash is the same, sort by count (descending)
        return b.count - a.count;
      });
      
      // Update refs and state
      latestCrashDataRef.current = crashArray;
      setCrashData(crashArray);
    } catch (err) {
      console.error('Error fetching crash data:', err);
      setCrashError('Failed to fetch crash data');
    } finally {
      setLoadingCrashes(false);
    }
  };

  // Save fuzz directory to localStorage when it changes
  useEffect(() => {
    if (fuzzDirectory) {
      localStorage.setItem('autoFuzzDirectory', fuzzDirectory);
    }
  }, [fuzzDirectory]);

  const handleDirectoryChange = (event) => {
    setFuzzDirectory(event.target.value);
  };

  const handleTabChange = (event, newValue) => {
    setCurrentTab(newValue);
  };

  // Helper function to truncate hash for display (16 characters)
  const truncateHash = (hash) => {
    if (!hash) return '';
    return hash.substring(0, 16);
  };

  // Helper function to format stage number (remove "stage_" prefix)
  const formatStage = (stageStr) => {
    if (!stageStr) return '';
    return stageStr.replace('stage_', '');
  };

  // Helper function to get status icon and tooltip text
  const getStatusIcon = (status) => {
    switch (status) {
      case CRASH_STATUS.NEW:
        return {
          icon: <NewReleasesIcon color="error" />,
          tooltip: 'New crash: Found in the most recent stage, not yet triaged or patched'
        };
      case CRASH_STATUS.NEW_RARE:
        return {
          icon: <WarningIcon sx={{ color: 'warning.main' }} />,
          tooltip: 'Rare crash: Found in the most recent stage with 4 or fewer instances, not yet triaged or patched'
        };
      case CRASH_STATUS.TRIAGED:
        return {
          icon: <AssignmentIcon color="primary" />,
          tooltip: 'Triaged: Found in the most recent stage, has been triaged but not patched'
        };
      case CRASH_STATUS.PATCH_PENDING:
        return {
          icon: <BuildIcon color="warning" />,
          tooltip: 'Patch pending: Found in the most recent stage, patch has been created'
        };
      case CRASH_STATUS.FAILED_PATCH:
        return {
          icon: <WarningIcon sx={{ color: 'error.main', transform: 'scale(1.2)' }} />,
          tooltip: 'Failed patch: Was patched in an earlier stage but still appears in a later stage'
        };
      case CRASH_STATUS.OBSOLETE_PATCHED:
        return {
          icon: <CheckCircleIcon color="success" />,
          tooltip: 'Obsolete (patched): Not in the most recent stage, has been patched'
        };
      case CRASH_STATUS.OBSOLETE_REPLACED:
        return {
          icon: <HourglassEmptyIcon sx={{ color: 'text.disabled', opacity: 0.7 }} />,
          tooltip: 'Obsolete (replaced): Not in the most recent stage, appears in a newer stage'
        };
      case CRASH_STATUS.OBSOLETE:
        return {
          icon: <HourglassEmptyIcon color="disabled" />,
          tooltip: 'Obsolete: Not in the most recent stage'
        };
      default:
        return {
          icon: null,
          tooltip: 'Unknown status'
        };
    }
  };

  // Helper functions for patch data
  const handleOpenPatchDialog = (patchData, title, crashHash, crashStage) => {
    setCurrentPatchData(patchData);
    setPatchDialogTitle(title);
    setCurrentCrashStage(crashStage);
    
    // If it's an endpoint fix, find the original template
    if (patchData.endpoint_name && fuzzerSchemaData[crashStage]) {
      const schema = fuzzerSchemaData[crashStage];
      const endpoint = schema.endpoints.find(e => e.name === patchData.endpoint_name);
      
      if (endpoint) {
        setOriginalTemplate(endpoint.template);
      } else {
        setOriginalTemplate('Original template not found');
      }
    } else {
      setOriginalTemplate('');
    }
    
    setPatchDialogOpen(true);
  };

  const handleClosePatchDialog = () => {
    setPatchDialogOpen(false);
    setOriginalTemplate('');
    setCurrentCrashStage('');
  };

  // Helper function to determine if patch is a filter or fixes
  const isPatchFilter = (patchData) => {
    return patchData && patchData.fix && patchData.fix.filter_attempt;
  };

  // Helper function to get patch buttons
  const getPatchButtons = (crash) => {
    if (!crash.hasPatch || !crash.patchData) return '-';
    
    const patch = crash.patchData;

    if (isPatchFilter(patch)) {
      // It's a filter patch
      return (
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <FilterAltIcon fontSize="small" sx={{ mr: 0.5, color: 'text.secondary' }} />
          <Typography 
            component="span" 
            sx={{ 
              cursor: 'pointer', 
              color: 'primary.main',
              '&:hover': { textDecoration: 'underline' }
            }}
            onClick={() => handleOpenPatchDialog(patch, 'Filter Function', crash.hash, crash.stage)}
          >
            View Filter
          </Typography>
        </Box>
      );
    } else if (patch.fix && patch.fix.fixes && patch.fix.fixes.length > 0) {
      // It's an endpoint fixes patch
      const fixes = patch.fix.fixes;
      
      return (
        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
          {fixes.map((fix, index) => (
            <Box 
              key={index}
              sx={{ 
                display: 'flex', 
                alignItems: 'center',
                mb: index < fixes.length - 1 ? 0.5 : 0,
                cursor: 'pointer',
                '&:hover': { 
                  '& .MuiTypography-root': { textDecoration: 'underline' } 
                }
              }}
              onClick={() => handleOpenPatchDialog(fix, `Fix for ${fix.endpoint_name}`, crash.hash, crash.stage)}
            >
              <CodeIcon fontSize="small" sx={{ mr: 0.5, color: 'text.secondary' }} />
              <Typography 
                component="span" 
                sx={{ 
                  color: 'primary.main',
                  fontSize: '0.875rem',
                }}
              >
                {fix.endpoint_name}
              </Typography>
            </Box>
          ))}
        </Box>
      );
    }
    
    return '-';
  };

  // Helper function to render template with highlighted differences
  const renderTemplateWithDiff = (oldTemplate, newTemplate) => {
    try {
      // If diffLines is not available, fall back to simple rendering
      if (!diffLines) {
        return (
          <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, mb: 2 }}>
            <Box sx={{ flex: 1, mr: { xs: 0, md: 1 }, mb: { xs: 2, md: 0 } }}>
              <Typography variant="subtitle2" gutterBottom>Original Template (from {currentCrashStage}):</Typography>
              <Box 
                component="pre" 
                sx={{ 
                  bgcolor: 'background.paper', 
                  p: 2, 
                  borderRadius: 1, 
                  overflow: 'auto',
                  maxHeight: '400px',
                  border: '1px solid',
                  borderColor: 'divider',
                  fontSize: '0.875rem',
                  fontFamily: 'monospace'
                }}
              >
                {oldTemplate}
              </Box>
            </Box>
            <Box sx={{ flex: 1, ml: { xs: 0, md: 1 } }}>
              <Typography variant="subtitle2" gutterBottom>New Template:</Typography>
              <Box 
                component="pre" 
                sx={{ 
                  bgcolor: 'background.paper', 
                  p: 2, 
                  borderRadius: 1, 
                  overflow: 'auto',
                  maxHeight: '400px',
                  border: '1px solid',
                  borderColor: 'divider',
                  fontSize: '0.875rem',
                  fontFamily: 'monospace'
                }}
              >
                {newTemplate}
              </Box>
            </Box>
          </Box>
        );
      }
      
      // Calculate the diff
      const diff = diffLines(oldTemplate, newTemplate);
      
      // Render the diff
      return (
        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, mb: 2 }}>
          <Box sx={{ flex: 1, mr: { xs: 0, md: 1 }, mb: { xs: 2, md: 0 } }}>
            <Typography variant="subtitle2" gutterBottom>Original Template (from {currentCrashStage}):</Typography>
            <Box 
              component="pre" 
              sx={{ 
                bgcolor: 'background.paper', 
                p: 2, 
                borderRadius: 1, 
                overflow: 'auto',
                maxHeight: '400px',
                border: '1px solid',
                borderColor: 'divider',
                fontSize: '0.875rem',
                fontFamily: 'monospace'
              }}
            >
              {diff.map((part, index) => (
                <Box 
                  key={index} 
                  component="span" 
                  sx={{ 
                    color: part.added ? 'transparent' : (part.removed ? 'error.dark' : 'inherit'),
                    bgcolor: part.removed ? 'rgba(255, 0, 0, 0.05)' : 'inherit',
                    textDecoration: part.removed ? 'line-through' : 'none',
                    opacity: part.added ? 0 : 1,
                    display: part.added ? 'none' : 'inline'
                  }}
                >
                  {part.value}
                </Box>
              ))}
            </Box>
          </Box>
          <Box sx={{ flex: 1, ml: { xs: 0, md: 1 } }}>
            <Typography variant="subtitle2" gutterBottom>New Template:</Typography>
            <Box 
              component="pre" 
              sx={{ 
                bgcolor: 'background.paper', 
                p: 2, 
                borderRadius: 1, 
                overflow: 'auto',
                maxHeight: '400px',
                border: '1px solid',
                borderColor: 'divider',
                fontSize: '0.875rem',
                fontFamily: 'monospace'
              }}
            >
              {diff.map((part, index) => (
                <Box 
                  key={index} 
                  component="span" 
                  sx={{ 
                    color: part.removed ? 'transparent' : (part.added ? 'success.dark' : 'inherit'),
                    bgcolor: part.added ? 'rgba(0, 200, 0, 0.05)' : 'inherit',
                    opacity: part.removed ? 0 : 1,
                    display: part.removed ? 'none' : 'inline'
                  }}
                >
                  {part.value}
                </Box>
              ))}
            </Box>
          </Box>
        </Box>
      );
    } catch (error) {
      console.error('Error rendering diff:', error);
      
      // Fallback to simple rendering
      return (
        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, mb: 2 }}>
          <Box sx={{ flex: 1, mr: { xs: 0, md: 1 }, mb: { xs: 2, md: 0 } }}>
            <Typography variant="subtitle2" gutterBottom>Original Template (from {currentCrashStage}):</Typography>
            <Box 
              component="pre" 
              sx={{ 
                bgcolor: 'background.paper', 
                p: 2, 
                borderRadius: 1, 
                overflow: 'auto',
                maxHeight: '400px',
                border: '1px solid',
                borderColor: 'divider',
                fontSize: '0.875rem',
                fontFamily: 'monospace'
              }}
            >
              {oldTemplate}
            </Box>
          </Box>
          <Box sx={{ flex: 1, ml: { xs: 0, md: 1 } }}>
            <Typography variant="subtitle2" gutterBottom>New Template:</Typography>
            <Box 
              component="pre" 
              sx={{ 
                bgcolor: 'background.paper', 
                p: 2, 
                borderRadius: 1, 
                overflow: 'auto',
                maxHeight: '400px',
                border: '1px solid',
                borderColor: 'divider',
                fontSize: '0.875rem',
                fontFamily: 'monospace'
              }}
            >
              {newTemplate}
            </Box>
          </Box>
        </Box>
      );
    }
  };

  // Helper function to render patch content
  const renderPatchContent = () => {
    if (!currentPatchData) return null;
    
    if (currentPatchData.endpoint_name) {
      // It's an endpoint fix
      return (
        <>
          <Typography variant="subtitle1" gutterBottom>Explanation:</Typography>
          <Typography variant="body2" paragraph>{currentPatchData.explanation}</Typography>
          
          {originalTemplate && (
            <>
              <Typography variant="subtitle1" gutterBottom>Template Comparison:</Typography>
              {renderTemplateWithDiff(originalTemplate, currentPatchData.new_template)}
            </>
          )}
          
          {!originalTemplate && (
            <>
              <Typography variant="subtitle1" gutterBottom>New Template:</Typography>
              <Box 
                component="pre" 
                sx={{ 
                  bgcolor: 'background.paper', 
                  p: 2, 
                  borderRadius: 1, 
                  overflow: 'auto',
                  maxHeight: '400px',
                  border: '1px solid',
                  borderColor: 'divider',
                  fontSize: '0.875rem',
                  fontFamily: 'monospace'
                }}
              >
                {currentPatchData.new_template}
              </Box>
            </>
          )}
        </>
      );
    } else if (currentPatchData.fix && currentPatchData.fix.filter_attempt) {
      // It's a filter function
      const filterData = currentPatchData.fix.filter_attempt;
      return (
        <>
          <Typography variant="subtitle1" gutterBottom>Summary:</Typography>
          <Typography variant="body2" paragraph>{filterData.summary_line}</Typography>
          
          <Typography variant="subtitle1" gutterBottom>Explanation:</Typography>
          <Typography variant="body2" paragraph>{filterData.explanation}</Typography>
          
          <Typography variant="subtitle1" gutterBottom>Filter Function:</Typography>
          <Box 
            component="pre" 
            sx={{ 
              bgcolor: 'background.paper', 
              p: 2, 
              borderRadius: 1, 
              overflow: 'auto',
              maxHeight: '400px',
              border: '1px solid',
              borderColor: 'divider',
              fontSize: '0.875rem',
              fontFamily: 'monospace'
            }}
          >
            {filterData.filter_function}
          </Box>
          
          {currentPatchData.fix.precision && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="subtitle1" gutterBottom>Metrics:</Typography>
              <Typography variant="body2">Precision: {currentPatchData.fix.precision}</Typography>
              <Typography variant="body2">Recall: {currentPatchData.fix.recall}</Typography>
              <Typography variant="body2">F1 Score: {currentPatchData.fix.f1_score}</Typography>
            </Box>
          )}
        </>
      );
    }
    
    return <Typography>No patch data available</Typography>;
  };

  // Tab content components
  const SummaryTab = () => (
    <Box sx={{ p: 2 }}>
      <Typography variant="h6">Summary</Typography>
      <Typography variant="body2" color="text.secondary">
        Summary content will be displayed here.
      </Typography>
    </Box>
  );

  const CrashesTab = () => {
    // Filter crashes based on the hideObsoleteReplaced toggle
    const filteredCrashData = hideObsoleteReplaced 
      ? crashData.filter(crash => crash.status !== CRASH_STATUS.OBSOLETE_REPLACED)
      : crashData;
      
    return (
      <Box sx={{ p: 2 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h6" gutterBottom>Crashes</Typography>
          
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Typography variant="body2" sx={{ mr: 1 }}>
              Hide obsolete (replaced) crashes:
            </Typography>
            <Box 
              component="label" 
              sx={{ 
                display: 'inline-flex',
                alignItems: 'center',
                position: 'relative',
                cursor: 'pointer',
                width: '40px',
                height: '20px'
              }}
            >
              <Box 
                component="input"
                type="checkbox"
                checked={hideObsoleteReplaced}
                onChange={(e) => setHideObsoleteReplaced(e.target.checked)}
                sx={{ 
                  opacity: 0,
                  width: 0,
                  height: 0,
                  position: 'absolute'
                }}
              />
              <Box 
                sx={{ 
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  backgroundColor: hideObsoleteReplaced ? 'primary.main' : 'grey.400',
                  borderRadius: '10px',
                  transition: 'background-color 0.2s',
                  '&:before': {
                    content: '""',
                    position: 'absolute',
                    width: '16px',
                    height: '16px',
                    left: hideObsoleteReplaced ? '20px' : '2px',
                    bottom: '2px',
                    backgroundColor: 'common.white',
                    borderRadius: '50%',
                    transition: 'left 0.2s'
                  }
                }}
              />
            </Box>
          </Box>
        </Box>
        
        {loadingCrashes ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <CircularProgress />
          </Box>
        ) : crashError ? (
          <Typography color="error">{crashError}</Typography>
        ) : filteredCrashData.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No crashes found in the selected directory.
          </Typography>
        ) : (
          <TableContainer component={Paper}>
            <Table sx={{ minWidth: 650 }} aria-label="crashes table">
              <TableHead>
                <TableRow>
                  <TableCell width="60px">
                    <Tooltip title="Crash status: Indicates whether the crash is new, triaged, patched, or obsolete" arrow placement="top">
                      <Typography>Status</Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Tooltip title="Unique identifier for the crash (truncated to 16 characters)" arrow placement="top">
                      <Typography>Crash Hash</Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell>Crash Name</TableCell>
                  <TableCell align="center">
                    <Tooltip title="Stage where this crash instance was observed" arrow placement="top">
                      <Typography>Stage</Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="Number of crash instances found in this stage" arrow placement="top">
                      <Typography>Count</Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="center">
                    <Tooltip title="Whether the crash has been triaged" arrow placement="top">
                      <Typography>Triage</Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="center">
                    <Tooltip title="Whether a patch has been created for the crash" arrow placement="top">
                      <Typography>Patch</Typography>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredCrashData.map((crash, index) => {
                  const { icon, tooltip } = getStatusIcon(crash.status);
                  const isObsolete = crash.status === CRASH_STATUS.OBSOLETE || 
                                    crash.status === CRASH_STATUS.OBSOLETE_PATCHED || 
                                    crash.status === CRASH_STATUS.OBSOLETE_REPLACED;
                  const isFailedPatch = crash.status === CRASH_STATUS.FAILED_PATCH;
                  
                  return (
                    <TableRow
                      key={`${crash.hash}-${crash.stage}-${index}`}
                      sx={{ 
                        '&:last-child td, &:last-child th': { border: 0 },
                        opacity: isObsolete ? 0.6 : (isFailedPatch ? 0.9 : 1),
                        bgcolor: isFailedPatch ? 'rgba(255, 0, 0, 0.08)' : 'inherit'
                      }}
                    >
                      <TableCell>
                        <Tooltip title={tooltip} arrow>
                          <Box sx={{ display: 'flex', justifyContent: 'center' }}>
                            {icon}
                          </Box>
                        </Tooltip>
                      </TableCell>
                      <TableCell component="th" scope="row">
                        <Tooltip title={crash.hash} arrow>
                          <Typography fontFamily="monospace">{truncateHash(crash.hash)}</Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell>{crash.description}</TableCell>
                      <TableCell align="center">{formatStage(crash.stage)}</TableCell>
                      <TableCell align="right">{crash.count}</TableCell>
                      <TableCell align="center">
                        {crash.hasTriage && crash.triageData ? (
                          <Tooltip 
                            title={crash.triageData.explanation || "No explanation available"} 
                            arrow
                            placement="left"
                            sx={{ maxWidth: 500 }}
                          >
                            <Typography>Yes</Typography>
                          </Tooltip>
                        ) : (
                          <Typography color="text.secondary">-</Typography>
                        )}
                      </TableCell>
                      <TableCell align="center">
                        {getPatchButtons(crash)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Box>
    );
  };

  const PatchesTab = () => (
    <Box sx={{ p: 2 }}>
      <Typography variant="h6">Patches</Typography>
      <Typography variant="body2" color="text.secondary">
        Patches content will be displayed here.
      </Typography>
    </Box>
  );

  // Clean up any hover timers when component unmounts
  useEffect(() => {
    return () => {
      if (hoverTimerRef) {
        clearTimeout(hoverTimerRef);
      }
    };
  }, [hoverTimerRef]);

  return (
    <Box sx={{ flexGrow: 1, height: 'calc(100vh - 48px)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      {/* Topbar */}
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Grid container spacing={2} alignItems="center">
          <Grid item xs={12} sx={{ display: 'flex', alignItems: 'center' }}>
            <TextField
              sx={{ maxWidth: '700px', minWidth: '300px', flexGrow: 1 }}
              label="Fuzz Directory"
              value={fuzzDirectory}
              onChange={handleDirectoryChange}
              size="small"
              error={!!error}
            />
            <Box sx={{ ml: 2, mt: error ? '-12px' : '0px', minWidth: '120px', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center' }}>
              {loading ? (
                <Typography variant="body2" color="text.secondary">Loading stages...</Typography>
              ) : (
                <>
                  <Typography 
                    variant="body2" 
                    color={stageCount > 0 ? "primary" : "text.secondary"}
                    fontWeight={stageCount > 0 ? "medium" : "normal"}
                  >
                    {stageCount} stages found
                  </Typography>
                  <RefreshProgressIndicator 
                    interval={REFRESH_INTERVAL} 
                    onRefresh={handleRefresh} 
                  />
                </>
              )}
            </Box>
          </Grid>
        </Grid>
      </Box>
      
      {/* Nested Tab Bar */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs value={currentTab} onChange={handleTabChange} aria-label="autofuzz tabs">
          <Tab label="Summary" />
          <Tab label="Crashes" />
          <Tab label="Patches" />
        </Tabs>
      </Box>
      
      {/* Tab Content */}
      <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
        {currentTab === 0 && <SummaryTab />}
        {currentTab === 1 && <CrashesTab />}
        {currentTab === 2 && <PatchesTab />}
      </Box>

      {/* Patch Dialog */}
      <Dialog
        open={patchDialogOpen}
        onClose={handleClosePatchDialog}
        maxWidth="lg"
        fullWidth
        PaperProps={{
          sx: {
            minHeight: '70vh',
            maxHeight: '90vh',
            display: 'flex',
            flexDirection: 'column'
          }
        }}
      >
        <DialogTitle>
          {patchDialogTitle}
        </DialogTitle>
        <DialogContent dividers sx={{ flex: 1, overflow: 'auto' }}>
          {renderPatchContent()}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClosePatchDialog}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export default AutoFuzz; 