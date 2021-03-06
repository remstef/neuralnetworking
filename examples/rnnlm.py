# -*- coding: utf-8 -*-

# coding: utf-8

import sys
if not '..' in sys.path: sys.path.append('..')

import argparse
import time
import math
import os
from tqdm import tqdm
import torch
from torch.utils.data.sampler import BatchSampler, SequentialSampler, RandomSampler

import data
import nets.rnn
from embedding import Embedding, FastTextEmbedding, TextEmbedding, RandomEmbedding
from utils import Index, ShufflingBatchSampler, EvenlyDistributingSampler, SimpleSGD, createWrappedOptimizerClass

def parseSystemArgs():
  '''
  
  '''
  parser = argparse.ArgumentParser(description='PyTorch Wikitext-2 RNN/LSTM Language Model')
  parser.add_argument('--data', type=str, default='../data/wikisentences',
                      help='location of the data corpus')
  parser.add_argument('--model', type=str, default='LSTM',
                      help='type of recurrent net (RNN_TANH, RNN_RELU, LSTM, GRU)')
  parser.add_argument('--emsize', type=int, default=200,
                      help='size of word embeddings')
  parser.add_argument('--nhid', type=int, default=200,
                      help='number of hidden units per layer')
  parser.add_argument('--nlayers', type=int, default=2,
                      help='number of layers')
  parser.add_argument('--lr', type=float, default=20,
                      help='initial learning rate')
  parser.add_argument('--lr_decay', type=float, default=0.25,
                      help='decay amount of learning learning rate if no validation improvement occurs')
  parser.add_argument('--clip', type=float, default=0.25,
                      help='gradient clipping')
  parser.add_argument('--epochs', type=int, default=40,
                      help='upper epoch limit')
  parser.add_argument('--batch_size', type=int, default=20, metavar='N',
                      help='batch size') 
  parser.add_argument('--bptt', type=int, default=35,
                      help='sequence length') 
  parser.add_argument('--dropout', type=float, default=0.2,
                      help='dropout applied to layers (0 = no dropout)')
  parser.add_argument('--tied', action='store_true',
                      help='tie the word embedding and softmax weights')
  parser.add_argument('--seed', type=int, default=1111,
                      help='random seed')
  parser.add_argument('--cuda', action='store_true',
                      help='use CUDA')
  parser.add_argument('--log-interval', type=int, default=200, metavar='N',
                      help='report interval')
  parser.add_argument('--shuffle_batches', action='store_true',
                      help='shuffle batches')
  parser.add_argument('--shuffle_samples', action='store_true',
                      help='shuffle samples')
  parser.add_argument('--sequential_sampling', action='store_true',
                      help='use samples and batches sequentially.')
  parser.add_argument('--save', type=str, default='model.pt',
                      help='path to save the final model')
  parser.add_argument('--init_weights', type=str, default='',
                      help='path to initial embedding. emsize must match size of embedding')
  parser.add_argument('--chars', action='store_true',
                      help='use character sequences instead of token sequences')
  args = parser.parse_args()
  
  # Set the random seed manually for reproducibility.
  torch.manual_seed(args.seed)
  if torch.cuda.is_available():
    if not args.cuda:
      print('WARNING: You have a CUDA device, so you should probably run with --cuda')

  device = torch.device('cuda' if args.cuda else 'cpu')
  setattr(args, 'device', device)

  return args


def loadData(args):
  '''
  
  '''
  __SequenceDataset = data.CharSequence if args.chars else data.TokenSequence
  print(__SequenceDataset.__name__)
  index = Index(initwords = ['<unk>'], unkindex = 0)
  train_ = __SequenceDataset(args.data, subset='train.txt', index = index, seqlen = args.bptt, skip = args.bptt).to(args.device)
  index.freeze(silent = True).tofile(os.path.join(args.data, 'vocab_chars.txt' if args.chars else 'vocab_tokens.txt'))
  test_ = __SequenceDataset(args.data, subset='test.txt', index = index, seqlen = args.bptt, skip = args.bptt).to(args.device)
  valid_ = __SequenceDataset(args.data, subset='valid.txt', index = index, seqlen = args.bptt, skip = args.bptt).to(args.device)
  
  # load pre embedding
  if args.init_weights:
    # determine type of embedding by checking it's suffix
    if args.init_weights.endswith('bin'):
      preemb = FastTextEmbedding(args.init_weights, normalize = True).load()
      if args.emsize != preemb.dim():
        raise ValueError('emsize must match embedding size. Expected %d but got %d)' % (args.emsize, preemb.dim()))
    elif args.init_weights.endswith('txt'):
      preemb = TextEmbedding(args.init_weights, vectordim = args.emsize).load(normalize = True)
    elif args.init_weights.endswith('rand'):
      preemb = RandomEmbedding(vectordim = args.emsize)
    else:
      raise ValueError('Type of embedding cannot be inferred.')
    preemb = Embedding.filteredEmbedding(index.vocabulary(), preemb, fillmissing = True)
    preemb_weights = torch.Tensor(preemb.weights)
  else:
    preemb_weights = None
  
  eval_batch_size = 10
  __ItemSampler = RandomSampler if args.shuffle_samples else SequentialSampler
  __BatchSampler = BatchSampler if args.sequential_sampling else EvenlyDistributingSampler  
  train_loader = torch.utils.data.DataLoader(train_, batch_sampler = ShufflingBatchSampler(__BatchSampler(__ItemSampler(train_), batch_size=args.batch_size, drop_last = True), shuffle = args.shuffle_batches, seed = args.seed), num_workers = 0)
  test_loader = torch.utils.data.DataLoader(test_, batch_sampler = __BatchSampler(__ItemSampler(test_), batch_size=eval_batch_size, drop_last = True), num_workers = 0)
  valid_loader = torch.utils.data.DataLoader(valid_, batch_sampler = __BatchSampler(__ItemSampler(valid_), batch_size=eval_batch_size, drop_last = True), num_workers = 0)
  print(__ItemSampler.__name__)
  print(__BatchSampler.__name__)
  print('Shuffle training batches: ', args.shuffle_batches)

  setattr(args, 'index', index)
  setattr(args, 'ntokens', len(index))
  setattr(args, 'trainloader', train_loader)
  setattr(args, 'testloader', test_loader)
  setattr(args, 'validloader', valid_loader)
  setattr(args, 'preembweights', preemb_weights)
  setattr(args, 'eval_batch_size', eval_batch_size)

  return args

def buildModel(args):
  ###############################################################################
  # Build the model, define loss criteria and optimizer
  ###############################################################################
  ntokens = len(args.index)
  model = nets.rnn.RNNLM(
      rnn_type = args.model, 
      ntoken = ntokens, 
      ninp = args.emsize, 
      nhid = args.nhid, 
      nlayers = args.nlayers, 
      dropout = args.dropout, 
      tie_weights = args.tied, 
      init_em_weights = args.preembweights, 
      train_em_weights = True).to(args.device)
  criterion = torch.nn.CrossEntropyLoss()
  optimizer = createWrappedOptimizerClass(SimpleSGD)(model.parameters(), lr =args.lr, clip = args.clip)
  print(model)
  print(criterion)
  print(optimizer)
  
  setattr(args, 'model', model)
  setattr(args, 'criterion', criterion)
  setattr(args, 'optimizer', optimizer)
  
  return args

###############################################################################
# Training code
###############################################################################

def getprocessfun(args):
  model = args.model
  def process(batch_data):
    
    x_batch, y_batch, seqlengths, hidden, is_training = batch_data
    # reshape x and y batches so seqlen is dim 0 and batch is dim 1
    x_batch = x_batch.transpose(0,1) # switch dim 0 with dim 1
    y_batch = y_batch.transpose(0,1).contiguous()
          
    hidden = model.repackage_hidden(hidden)
    if is_training:
      model.zero_grad()
    outputs, hidden = model(x_batch, hidden, seqlengths)  
    outputs_flat = outputs.view(-1, args.ntokens)
    targets_flat = y_batch.view(-1)  
    loss = args.criterion(outputs_flat, targets_flat)
    return loss, (outputs_flat, hidden)
  return process

def evaluate(args, dloader):
  model = args.model
  # Turn on evaluation mode which disables dropout.
  model.eval()
  total_loss = 0.
  hidden = model.init_hidden(args.eval_batch_size)
  with torch.no_grad():
    for batch, batch_data in enumerate(tqdm(dloader, ncols=89, desc = 'Test ')):   
      batch_data.append(hidden)
      batch_data.append(False)
      loss, (outputs_flat, hidden) = process(batch_data)    
      loss_ = loss.item()
      current_loss = args.eval_batch_size * loss_
      total_loss += current_loss
  return total_loss / (len(dloader) * args.eval_batch_size )


def train(args):
  model = args.model
  # Turn on training mode which enables dropout.
  model.train()
  total_loss = 0.
  start_time = time.time()
  hidden = model.init_hidden(args.batch_size)
  
  for batch, batch_data in enumerate(tqdm(args.trainloader, ncols=89, desc='train')):
    batch_data.append(hidden)
    batch_data.append(True)
    model.zero_grad()
    loss, outputs_flat = process(batch_data)
    loss.backward()
    args.optimizer.step()
    total_loss += loss.item()

    if batch % args.log_interval == 0 and batch > 0:
      cur_loss = total_loss / args.log_interval
      elapsed = time.time() - start_time
      tqdm.write('| epoch {:3d} | batch {:5d} / {:5d} | lr {:02.2f} | ms/batch {:5.2f} | loss {:5.2f} | ppl {:8.2f}'.format(
          epoch, 
          batch, 
          len(args.trainloader), 
          args.optimizer.getLearningRate(),
          elapsed * 1000 / args.log_interval, 
          cur_loss, 
          math.exp(cur_loss)
          ))
      total_loss = 0
      start_time = time.time()

if __name__ == '__main__':
  # Loop over epochs.
  best_val_loss = None
  
  # At any point you can hit Ctrl + C to break out of training early.
  try:
  
    args = parseSystemArgs()
    args = loadData(args)
    args = buildModel(args)
    process = getprocessfun(args)
    
    for epoch in range(1, args.epochs+1):
      epoch_start_time = time.time()
      train(args)
      val_loss = evaluate(args, args.validloader)
      print('-' * 89)
      print('| end of epoch {:3d} | time: {:5.2f}s | valid loss {:5.2f} | valid ppl {:8.2f}'.format(
          epoch, 
          (time.time() - epoch_start_time), 
          val_loss, 
          math.exp(val_loss)
          ))
      print('-' * 89)
      # Save the model if the validation loss is the best we've seen so far.
      if not best_val_loss or val_loss < best_val_loss:
        with open(args.save, 'wb') as f:
          torch.save(args.model, f)
        best_val_loss = val_loss
      else:
          # Anneal the learning rate if no improvement has been seen in the validation dataset.
          args.optimizer.adjustLearningRate(1. / 4.)     

  
    # Load the best saved model.
    with open(args.save, 'rb') as f:
      model = torch.load(f)
      # after load the rnn params are not a continuous chunk of memory
      # this makes them a continuous chunk, and will speed up forward pass
      model.rnn.flatten_parameters()
    
    # Run on test data.
    #test_loss = evaluate(test_data)
    test_loss = evaluate(args, args.testloader)
    print('=' * 89)
    print('| End of training | test loss {:5.2f} | test ppl {:8.2f}'.format(
        test_loss, math.exp(test_loss)))
    print('=' * 89)

  except KeyboardInterrupt:
    print('-' * 89)
    print('Exiting from training early')  
