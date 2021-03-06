# -*- coding: utf-8 -*-

# coding: utf-8

import sys
if not '..' in sys.path: sys.path.append('..')

import rnnlm

import time
import math
import torch
import torchnet
from tqdm import tqdm

if __name__ == '__main__':
  
  try:
    
    args = rnnlm.parseSystemArgs()
    args = rnnlm.loadData(args)
    args = rnnlm.buildModel(args)  
    process = rnnlm.getprocessfun(args)
    model = args.model

    ###############################################################################
    # Set up Engine
    ###############################################################################
          
    def on_start(state):
      state['train_loss'] = 0.
      state['test_loss'] = 0.
      state['train_loss_per_interval'] = 0.
      state['best_val_loss'] = sys.maxsize
      if state['train']:
        state['hidden'] = model.init_hidden(args.batch_size)
      else:
        state['hidden'] = model.init_hidden(args.eval_batch_size)
    
    def on_end(state):
      pass
      
    def on_sample(state):
      state['sample'].append(state['hidden'])
      state['sample'].append(state['train'])
      state['batch_start_time'] = time.time()
      
    def on_forward(state):
      loss_val = state['loss'].item()
      _, state['hidden'] = state['output']
      state['train_loss'] += loss_val
      state['test_loss'] += loss_val
      state['train_loss_per_interval'] += loss_val
      if state['train']:        
        t = state['t']
        t_epoch = t % len(state['iterator'])
        if t_epoch % args.log_interval == 0 and t_epoch > 0:
          epoch = state['epoch']
          # maxepoch = state['maxepoch']
          t_epoch = t % len(state['iterator'])
          cur_loss = state['train_loss_per_interval'] / args.log_interval
          elapsed = time.time() - state['batch_start_time']
          tqdm.write('| epoch {:3d} | batch {:5d} / {:5d} | lr {:02.2f} | ms/batch {:5.2f} | loss {:5.2f} | ppl {:8.2f}'.format(
              epoch, 
              t_epoch,
              len(state['iterator']),
              args.optimizer.getLearningRate(),
              (elapsed * 1000) / args.log_interval, 
              cur_loss, 
              math.exp(cur_loss)
              ))
          state['train_loss_per_interval'] = 0.
      
    def on_start_epoch(state):
      state['epoch_start_time'] = time.time()
      state['train_loss'] = 0.
      state['train_loss_per_interval'] = 0.      
      model.train()
      state['hidden'] = model.init_hidden(args.batch_size)
      state['iterator'] = tqdm(state['iterator'], ncols=89, desc='train')
    
    def on_end_epoch(state):
      model.eval()
      test_state = engine.test(process, tqdm(args.validloader, ncols=89, desc='test '))
      val_loss = test_state['test_loss'] / len(test_state['iterator'])
      train_loss = state['train_loss'] / len(state['iterator'])
      
      print('++ Epoch {:03d} took {:06.2f}s (lr {:5.{lrprec}f}) ++ {:s}'.format(
          state['epoch'], 
          (time.time() - state['epoch_start_time']),
          args.optimizer.getLearningRate(),
          '-'*(49 if args.optimizer.getLearningRate() >= 1 else 47),
          lrprec=2 if args.optimizer.getLearningRate() >= 1 else 5))
      print('| train loss {:5.2f} | valid loss {:5.2f} | train ppl {:8.2f} | valid ppl {:8.2f}'.format( 
          train_loss, 
          val_loss,
          math.exp(train_loss),
          math.exp(val_loss)
          ))
      print('-' * 89)
      
       # Save the model if the validation loss is the best we've seen so far.
      if val_loss < state['best_val_loss']:
        state['best_val_loss'] = val_loss
        with open(args.save, 'wb') as f:
          torch.save(model, f)
      else:
        # Anneal the learning rate if no improvement has been seen in the validation dataset.
        args.optimizer.adjustLearningRate(factor = args.lr_decay)
  
    # define engine 
    engine = torchnet.engine.Engine()  
    engine.hooks['on_start'] = on_start
    engine.hooks['on_start_epoch'] = on_start_epoch  
    engine.hooks['on_sample'] = on_sample
    engine.hooks['on_forward'] = on_forward
    engine.hooks['on_end_epoch'] = on_end_epoch
      
    ###############################################################################
    # run training
    ###############################################################################
    
    final_state = engine.train(process, args.trainloader, maxepoch=args.epochs, optimizer=args.optimizer)
    
    # Load the best saved model.
    with open(args.save, 'rb') as f:
      model = torch.load(f)
      model.rnn.flatten_parameters()   # after load the rnn params are not a continuous chunk of memory. This makes them a continuous chunk, and will speed up forward pass
    
    # Run on test data.
    model.eval()
    test_state = engine.test(process, tqdm(args.testloader, ncols=89, desc='test'))
    test_loss = test_state['test_loss'] / len(test_state['iterator'])
    print('++ End of training ++ ' + '='*67)
    print('| test loss {:5.2f} | test ppl {:8.2f} '.format(
        test_loss, 
        math.exp(test_loss)))
    print('=' * 89)
  
  except (KeyboardInterrupt, SystemExit):
    print('Process cancelled')
  
