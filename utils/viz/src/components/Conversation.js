import React, { useState } from 'react';
import { Box, Paper, Typography, IconButton, Collapse } from '@mui/material';
import { Light as SyntaxHighlighter } from 'react-syntax-highlighter';
import js from 'react-syntax-highlighter/dist/esm/languages/hljs/javascript';
import python from 'react-syntax-highlighter/dist/esm/languages/hljs/python';
import json from 'react-syntax-highlighter/dist/esm/languages/hljs/json';
import cpp from 'react-syntax-highlighter/dist/esm/languages/hljs/cpp';
import { vs } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';

// Register languages
SyntaxHighlighter.registerLanguage('javascript', js);
SyntaxHighlighter.registerLanguage('python', python);
SyntaxHighlighter.registerLanguage('json', json);
SyntaxHighlighter.registerLanguage('cpp', cpp);

const detectLanguage = (content) => {
  if (content.includes('def ') || content.includes('import ') || content.includes('print(')) {
    return 'python';
  }
  if (content.includes('function') || content.includes('const ') || content.includes('let ')) {
    return 'javascript';
  }
  // C++ detection
  if (content.includes('#include') || content.includes('int main') || 
      content.includes('std::') || content.includes('->') || 
      content.includes('public:') || content.includes('private:')) {
    return 'cpp';
  }
  try {
    JSON.parse(content);
    return 'json';
  } catch {
    return null;
  }
};

const isTemplateResponse = (content) => {
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === 'object' && 'template' in parsed;
  } catch {
    return false;
  }
};

const isDetailedTemplateResponse = (content) => {
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === 'object' && 
           'function_name' in parsed && 
           'function_args' in parsed && 
           'function_body' in parsed;
  } catch {
    return false;
  }
};

const Message = ({ role, content }) => {
  const [expanded, setExpanded] = useState(true);
  const isTemplate = role === 'assistant' && isTemplateResponse(content);
  const isDetailedTemplate = role === 'assistant' && isDetailedTemplateResponse(content);
  const isCode = content.includes('```');
  
  const formatContent = (content) => {
    // Handle detailed template responses
    if (isDetailedTemplate) {
      try {
        const { function_name, function_args, function_body } = JSON.parse(content);
        const formattedArgs = function_args.join(', ');
        const functionCode = `${function_name}(${formattedArgs}) {
  ${function_body}
}`;
        return (
          <Box>
            <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 0.5 }}>
              C++ Function
            </Typography>
            <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
              <SyntaxHighlighter
                language="cpp"
                style={vs}
                customStyle={{ margin: 0, background: '#f5f5f5' }}
              >
                {functionCode}
              </SyntaxHighlighter>
            </Paper>
          </Box>
        );
      } catch (error) {
        console.error('Error parsing detailed template:', error);
        return <Typography color="error">Error parsing detailed template response</Typography>;
      }
    }

    // Handle template responses
    if (isTemplate) {
      try {
        const { template } = JSON.parse(content);
        return (
          <Box>
            <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 0.5 }}>
              C++ Template
            </Typography>
            <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
              <SyntaxHighlighter
                language="cpp"
                style={vs}
                customStyle={{ margin: 0, background: '#f5f5f5' }}
              >
                {template}
              </SyntaxHighlighter>
            </Paper>
          </Box>
        );
      } catch (error) {
        console.error('Error parsing template:', error);
        return <Typography color="error">Error parsing template response</Typography>;
      }
    }

    // Handle regular messages
    if (!isCode) {
      return (
        <Typography
          component="div"
          sx={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontFamily: role === 'system' ? 'monospace' : 'inherit',
            fontSize: role === 'system' ? '0.9em' : 'inherit',
          }}
        >
          {content}
        </Typography>
      );
    }

    const parts = content.split('```');
    return parts.map((part, index) => {
      if (index % 2 === 0) {
        return part.trim() ? (
          <Typography
            key={index}
            component="div"
            sx={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              mb: 1,
              mt: index > 0 ? 1 : 0
            }}
          >
            {part}
          </Typography>
        ) : null;
      } else {
        const codeContent = part.trim();
        const firstNewline = codeContent.indexOf('\n');
        const language = firstNewline > -1 ? codeContent.slice(0, firstNewline).trim() : null;
        const code = firstNewline > -1 ? codeContent.slice(firstNewline + 1) : codeContent;
        
        return (
          <Box key={index} sx={{ my: 1 }}>
            {language && (
              <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 0.5 }}>
                {language}
              </Typography>
            )}
            <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
              <SyntaxHighlighter
                language={language || detectLanguage(code) || 'text'}
                style={vs}
                customStyle={{ margin: 0, background: '#f5f5f5' }}
              >
                {code}
              </SyntaxHighlighter>
            </Paper>
          </Box>
        );
      }
    });
  };

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: role === 'user' ? 'flex-end' : 'flex-start',
        mb: 2,
      }}
    >
      <Paper
        elevation={1}
        sx={{
          maxWidth: '85%',
          p: 2,
          bgcolor: role === 'user' ? '#e3f2fd' : role === 'system' ? '#f5f5f5' : '#fff',
          borderRadius: 2,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
          <Typography 
            variant="subtitle2" 
            sx={{ 
              color: role === 'system' ? 'text.secondary' : 'primary.main',
              fontWeight: 'medium'
            }}
          >
            {role.charAt(0).toUpperCase() + role.slice(1)}
            {isTemplate && ' (Template)'}
          </Typography>
          <IconButton size="small" onClick={() => setExpanded(!expanded)} sx={{ ml: 1 }}>
            {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          </IconButton>
        </Box>
        <Collapse in={expanded}>
          <Box>{formatContent(content)}</Box>
        </Collapse>
      </Paper>
    </Box>
  );
};

const Conversation = ({ messages }) => {
  return (
    <Box sx={{ p: 2 }}>
      {messages.map((message, index) => (
        <Message key={index} {...message} />
      ))}
    </Box>
  );
};

export default Conversation; 